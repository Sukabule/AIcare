import asyncio
import json
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from starlette.concurrency import run_in_threadpool

from app.core.memory import make_token_counter, trim_history
from app.core.prompts import AGENT_SYSTEM
from app.schemas.agent import AgentResult, ToolResult
from app.tools.infra import ToolExecutor
from app.tools.registry import get_all_tools


@dataclass
class PreparedTurn:
    conversation_id: int
    messages: list
    ai: AIMessage
    tool_results: list[ToolResult]
    citations: list[dict]
    refusal: str | None


def replay_turns(rows):
    """Discard unfinished turns and tool preambles from cross-turn model context."""
    history, pending = [], None
    for row in rows:
        if row.role == "user":
            pending = HumanMessage(content=row.content or "")
        elif row.role == "assistant" and row.content and not row.tool_calls and pending:
            history.extend([pending, AIMessage(content=row.content)])
            pending = None
    return history


class AgentService:
    def __init__(
        self,
        repository,
        model,
        registry,
        *,
        token_counter=None,
        max_tokens=2000,
        tool_timeout_seconds=10,
        tool_read_retries=1,
        direct_answer_guard=None,
    ):
        self.repository = repository
        self.model = model
        self.registry = registry
        self.counter = token_counter or make_token_counter()
        self.max_tokens = max_tokens
        self.direct_answer_guard = direct_answer_guard
        self.executor = ToolExecutor(registry, tool_timeout_seconds, tool_read_retries)
        self._locks = {}

    @asynccontextmanager
    async def _lock(self, conversation_id):
        entry = self._locks.setdefault(conversation_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._locks.pop(conversation_id, None)

    async def _prepare_turn(self, body):
        """Emit tool status before executing, then yield the shared prepared turn.

        The lock stays held while each caller performs final generation. Consumers
        use aclosing to release it on cancellation or early generator close.
        """
        conversation = await self.repository.get_or_create(body.user_id, body.conversation_id)
        cid = conversation.id
        async with self._lock(cid):
            await self.repository.append(cid, "user", body.message)
            rows = await self.repository.history(cid)
            messages = await run_in_threadpool(
                trim_history,
                replay_turns(rows[:-1]),
                SystemMessage(content=AGENT_SYSTEM),
                body.message,
                max_tokens=self.max_tokens,
                token_counter=self.counter,
            )
            ai = await self.model.bind_tools(await get_all_tools(self.registry)).ainvoke(messages)
            if ai.invalid_tool_calls:
                raise ValueError("Invalid upstream tool call")
            if not ai.tool_calls and self.direct_answer_guard:
                refusal = await self.direct_answer_guard(cid, body.message, ai.text)
                if refusal:
                    ai = AIMessage(content=refusal)
            await self.repository.append(
                cid, "assistant", ai.text or None, tool_calls=ai.tool_calls or None
            )
            for call in ai.tool_calls:
                yield {"event": "tool", "name": call["name"]}
            results = await asyncio.gather(
                *[self.executor.execute(call, cid, body.message) for call in ai.tool_calls]
            )
            tool_messages, citations = [], []
            refusal = None
            for result in results:
                if result.name == "query_faq" and not result.ok:
                    refusal = "知识检索服务暂时不可用，无法核实该问题。请稍后重试或联系人工客服。"
                if result.ok and isinstance(result.data, dict):
                    for citation in result.data.get("citations", []):
                        citation["number"] = len(citations) + 1
                        citations.append(citation)
                    if result.data.get("refuse"):
                        refusal = result.data["message"]
                content = json.dumps(result.model_dump(), ensure_ascii=False)
                await self.repository.append(cid, "tool", content, tool_call_id=result.tool_call_id)
                tool_messages.append(
                    ToolMessage(content=content, tool_call_id=result.tool_call_id, name=result.name)
                )
            if citations:
                yield {"event": "citations", "citations": citations}
            yield PreparedTurn(
                cid, [*messages, ai, *tool_messages], ai, list(results), citations, refusal
            )

    async def run_agent_turn(self, body) -> AgentResult:
        async with aclosing(self._prepare_turn(body)) as preparation:
            async for item in preparation:
                if isinstance(item, dict):
                    continue
                answer = item.ai.text
                if item.refusal:
                    answer = item.refusal
                    await self.repository.append(item.conversation_id, "assistant", answer)
                elif item.ai.tool_calls:
                    final = await self.model.ainvoke(item.messages)
                    if final.tool_calls or final.invalid_tool_calls:
                        raise ValueError("Unexpected second tool round")
                    answer = final.text
                    if not answer:
                        raise ValueError("Empty final response")
                    await self.repository.append(item.conversation_id, "assistant", answer)
                if not answer:
                    raise ValueError("Empty response")
                result = AgentResult(
                    conversation_id=item.conversation_id,
                    answer=answer,
                    tool_calls=item.ai.tool_calls,
                    tool_results=item.tool_results,
                    citations=item.citations,
                )
        return result

    async def stream_agent_turn(self, body):
        async with aclosing(self._prepare_turn(body)) as preparation:
            async for item in preparation:
                if isinstance(item, dict):
                    yield item
                    continue
                if item.refusal:
                    yield {"delta": item.refusal}
                    await self.repository.append(item.conversation_id, "assistant", item.refusal)
                elif item.ai.tool_calls:
                    parts = []
                    async with aclosing(self.model.astream(item.messages)) as final_stream:
                        async for chunk in final_stream:
                            if chunk.tool_call_chunks:
                                raise ValueError("Unexpected second tool round")
                            if chunk.text:
                                parts.append(chunk.text)
                                yield {"delta": chunk.text}
                    answer = "".join(parts)
                    if not answer:
                        raise ValueError("Empty final response")
                    await self.repository.append(item.conversation_id, "assistant", answer)
                else:
                    if not item.ai.text:
                        raise ValueError("Empty response")
                    yield {"delta": item.ai.text}
                yield {"event": "done", "conversation_id": item.conversation_id}
