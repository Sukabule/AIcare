import asyncio
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.core.context_log import logger
from app.core.memory import ConversationTooLong, layer_tokens, next_layer1_from
from app.core.observability import Observability
from app.core.summarizer import Summarizer
from app.graph.build import build_graph
from app.graph.nodes import Nodes
from app.schemas.agent import AgentResult


def message_delta(message, metadata):
    if metadata.get("langgraph_node") != "main_agent":
        return None
    if getattr(message, "tool_call_chunks", None) or getattr(message, "tool_calls", None):
        return None
    return {"delta": message.text} if message.text else None


def interrupted(value, cid):
    return {
        "event": "interrupt",
        "kind": value.value["type"],
        **{key: data for key, data in value.value.items() if key != "type"},
        "conversation_id": cid,
        "interrupt_id": value.id,
    }


class ResumeConflict(ValueError):
    pass


class GraphRuntime:
    def __init__(
        self,
        repository,
        model,
        rag,
        registry,
        settings,
        counter,
        classifier=None,
        reference_resolver=None,
        expander=None,
        intent_model=None,
        summary_model_factory=None,
    ):
        self.repository, self.settings = repository, settings
        self.nodes = Nodes(
            repository,
            model,
            rag,
            registry,
            settings,
            counter,
            classifier,
            reference_resolver,
            expander,
            intent_model,
        )
        self.graph = None
        self.observability = Observability(settings)
        self.nodes.observability = self.observability
        self._stack = AsyncExitStack()
        self._init_lock = asyncio.Lock()
        self._locks = {}
        self.summarizer = Summarizer(
            repository,
            summary_model_factory or (lambda: model),
            settings,
            counter,
            self.nodes.budget,
        )
        self.summarizer.observability = self.observability

    async def ensure(self):
        async with self._init_lock:
            if self.graph is None:
                path = Path(self.settings.graph_checkpoint_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                saver = await self._stack.enter_async_context(
                    AsyncSqliteSaver.from_conn_string(str(path))
                )
                await saver.setup()
                self.graph = self.observability.bind(build_graph(self.nodes, saver))
        return self.graph

    async def close(self):
        await self.summarizer.close()
        await self.observability.flush()
        await self._stack.aclose()
        self.graph = None

    def config(self, cid):
        return {
            "configurable": {"thread_id": str(cid)},
            "recursion_limit": self.settings.max_agent_steps * 2 + 16,
            "metadata": {"langfuse_session_id": str(cid)},
        }

    @asynccontextmanager
    async def locked(self, cid):
        entry = self._locks.setdefault(cid, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._locks.pop(cid, None)

    @asynccontextmanager
    async def turn(self, body):
        if (
            self.nodes.counter([HumanMessage(content=body.message)])
            > self.settings.max_user_input_tokens
        ):
            raise ConversationTooLong("本轮输入超过 MAX_USER_INPUT_TOKENS，请缩短问题。")
        conversation = await self.repository.get_or_create(body.user_id, body.conversation_id)
        cid = conversation.id
        async with self.locked(cid):
            graph, config = await self.ensure(), self.config(cid)
            snapshot = await graph.aget_state(config)
            if snapshot.next:
                # A new question abandons a paused turn; never replay pending writes.
                await graph.aupdate_state(config, {}, as_node="log")
            row = await self.repository.append(cid, "user", body.message)
            messages = []
            if not snapshot.values:
                # Restore all audited messages if no checkpoint exists, including tool pairs.
                names = {}
                for old in (await self.repository.history(cid))[:-1]:
                    if old.role == "user":
                        message = HumanMessage(content=old.content or "", id=f"db-{old.id}")
                    elif old.role == "assistant":
                        calls = old.tool_calls or []
                        names.update({c["id"]: c["name"] for c in calls})
                        message = AIMessage(
                            content=old.content or "", tool_calls=calls, id=f"db-{old.id}"
                        )
                    else:
                        message = ToolMessage(
                            content=old.content or "",
                            tool_call_id=old.tool_call_id,
                            name=names.get(old.tool_call_id),
                            id=f"db-{old.id}",
                        )
                    messages.append(message)
            state = {
                "messages": [*messages, HumanMessage(content=body.message, id=f"db-{row.id}")],
                "summary": conversation.summary or "",
                "summary_upto_msg_id": conversation.summary_upto_msg_id or 0,
                "layer1_from_msg_id": conversation.layer1_from_msg_id or 0,
                "user_id": body.user_id,
                "conversation_id": cid,
                "question": body.message,
                "resolved_question": "",
                "resolved_query": "",
                "intent": "",
                "intent_confidence": 0.0,
                "route": "",
                "answer": "",
                "evidence": "",
                "citations": [],
                "order_id": "",
                "order_data": {},
                "expanded_queries": [],
                "refund_ticket_no": "",
                "evidence_strong": True,
                "gate_source": "",
                "gate_reason": "",
                "steps": 0,
                "tokens_used": 0,
                "max_agent_steps": self.settings.max_agent_steps,
                "suggested_actions": [],
                "tool_calls": [],
                "tool_results": [],
                "evidence_confidence": 0.0,
                "fallback_source": "",
                "retrieved_snapshot": [],
                "proposal_stop": False,
                "trace": {"nodes": [], "retrieved": False, "fallback": False},
            }
            with self.observability.request_scope(
                cid, input={"question": body.message, "conversation_id": cid}
            ) as trace_id:
                state["trace"]["langfuse_trace_id"] = trace_id
                yield graph, state, config

    @asynccontextmanager
    async def resume(self, cid, order_id=None, interrupt_id=None, *, confirmed=None):
        async with self.locked(cid):
            graph, config = await self.ensure(), self.config(cid)
            snapshot = await graph.aget_state(config)
            pending = [i for task in snapshot.tasks for i in task.interrupts]
            if len(pending) != 1:
                raise ResumeConflict("该会话没有待确认的操作，请重新发送问题。")
            item = pending[0]
            if interrupt_id is not None and interrupt_id != item.id:
                raise ResumeConflict("这张确认卡已过期，请使用最新一轮的卡片。")
            kind = item.value["type"]
            if kind == "select_order" and "fetch_order" in snapshot.next:
                if confirmed is not None or order_id not in {
                    o["order_id"] for o in item.value["orders"]
                }:
                    raise ResumeConflict("请选择列表中的订单。")
                choice = order_id
            elif kind == "confirm_ticket" and "agent_tools" in snapshot.next:
                if order_id is not None or type(confirmed) is not bool:
                    raise ResumeConflict("请确认提交或取消工单。")
                choice = {"confirmed": confirmed}
            else:
                raise ResumeConflict("该操作已失效，请重新发送问题。")
            await self.repository.get_or_create(snapshot.values["user_id"], cid)
            with self.observability.request_scope(
                cid,
                input={"question": snapshot.values.get("question"), "resume": choice, "kind": kind},
            ):
                await self.observability.intent(
                    snapshot.values.get("intent", ""),
                    snapshot.values.get("intent_confidence", 0.0),
                    cid,
                )
                yield graph, Command(resume=choice), config

    @staticmethod
    def result(state):
        keys = (
            "conversation_id",
            "answer",
            "tool_calls",
            "tool_results",
            "citations",
            "suggested_actions",
            "trace",
        )
        values = {k: state[k] for k in keys}
        items = state.get("__interrupt__", ())
        if items:
            values["interrupt"] = interrupted(items[0], state["conversation_id"])
        return AgentResult(**values)

    async def run_agent_turn(self, body):
        async with self.turn(body) as (graph, state, config):
            result = await graph.ainvoke(state, config)
            if not result.get("__interrupt__"):
                await self.finish_turn(result)
            else:
                self.observability.record_output(
                    {
                        "interrupt": interrupted(
                            result["__interrupt__"][0], result["conversation_id"]
                        )
                    }
                )
            return self.result(result)

    async def resume_turn(self, cid, order_id=None, interrupt_id=None, *, confirmed=None):
        async with self.resume(cid, order_id, interrupt_id, confirmed=confirmed) as (
            graph,
            command,
            config,
        ):
            result = await graph.ainvoke(command, config)
            if not result.get("__interrupt__"):
                await self.finish_turn(result)
            else:
                self.observability.record_output(
                    {
                        "interrupt": interrupted(
                            result["__interrupt__"][0], result["conversation_id"]
                        )
                    }
                )
            return self.result(result)

    async def stream(self, graph, data, config, cid):
        streamed, last_citations = False, []
        async with aclosing(
            graph.astream(data, config, stream_mode=["messages", "updates"])
        ) as stream:
            async for mode, value in stream:
                if mode == "messages":
                    event = message_delta(*value)
                    if event:
                        streamed = True
                        yield event
                    continue
                for node, update in value.items():
                    if node == "__interrupt__":
                        event = interrupted(update[0], cid)
                        self.observability.record_output({"interrupt": event})
                        yield event
                        return
                    if node == "classify_intent" and update["route"] == "knowledge":
                        yield {"event": "tool", "name": "query_faq"}
                    if node == "fetch_order":
                        yield {"event": "tool", "name": "query_order"}
                        yield {"event": "tool", "name": "query_faq"}
                    citations = update.get("citations")
                    if citations and citations != last_citations:
                        last_citations = citations
                        yield {"event": "citations", "citations": citations}
                    if node == "main_agent":
                        ai = update["messages"][-1]
                        for call in ai.tool_calls:
                            yield {"event": "tool", "name": call["name"]}
                        if ai.tool_calls:
                            streamed = False
                        elif ai.text and not streamed:
                            yield {"delta": ai.text}
                    if node in ("script_reply", "complaint_reply", "fallback_reply"):
                        yield {"delta": update["answer"]}
                    if node == "agent_tools" and update.get("answer"):
                        yield {"delta": update["answer"]}
                    if update.get("suggested_actions"):
                        yield {"event": "actions", "items": update["suggested_actions"]}
        try:
            await self.finish_turn((await graph.aget_state(config)).values)
        except Exception:
            logger.exception("layer_finish_failed; completed streamed reply unaffected")
        yield {"event": "done", "conversation_id": cid}

    async def settle_layers(self, state):
        try:
            _, recent = layer_tokens(
                state["messages"],
                state.get("summary_upto_msg_id", 0),
                state.get("layer1_from_msg_id", 0),
                token_counter=self.nodes.counter,
                keep_chars=self.settings.layer2_reply_keep_chars,
                tool_keep_tokens=self.settings.layer2_tool_keep_tokens,
            )
            if recent > self.nodes.budget.layer1:
                boundary = next_layer1_from(
                    state["messages"],
                    state.get("summary_upto_msg_id", 0),
                    self.nodes.budget.layer1,
                    token_counter=self.nodes.counter,
                )
                if boundary > state.get("layer1_from_msg_id", 0):
                    await self.repository.update_layer1_from(state["conversation_id"], boundary)
                    logger.info(
                        "layer_downgrade cid=%s old=%s new=%s layer1_tokens=%s budget=%s",
                        state["conversation_id"],
                        state.get("layer1_from_msg_id", 0),
                        boundary,
                        recent,
                        self.nodes.budget.layer1,
                    )
                    state = {**state, "layer1_from_msg_id": boundary}
        except Exception:
            logger.exception("layer_settlement_failed; reply unaffected")
        return state

    async def finish_turn(self, state):
        self.observability.record_output(state.get("answer", ""))
        state = await self.settle_layers(state)
        if trace_id := self.observability.current_trace_id():
            state = {
                **state,
                "trace": {
                    **state.get("trace", {}),
                    "langfuse_trace_id": trace_id,
                },
            }
        self.summarizer.maybe_schedule_summary(state)

    async def stream_agent_turn(self, body):
        async with self.turn(body) as (graph, state, config):
            async with aclosing(
                self.stream(graph, state, config, state["conversation_id"])
            ) as stream:
                async for event in stream:
                    yield event

    async def stream_resume_turn(self, cid, order_id=None, interrupt_id=None, *, confirmed=None):
        async with self.resume(cid, order_id, interrupt_id, confirmed=confirmed) as (
            graph,
            command,
            config,
        ):
            async with aclosing(self.stream(graph, command, config, cid)) as stream:
                async for event in stream:
                    yield event

    async def create_refund(self, cid, order_id, reason):
        async with self.locked(cid):
            graph, config = await self.ensure(), self.config(cid)
            snapshot = await graph.aget_state(config)
            state = snapshot.values
            proposals = state.get("suggested_actions", [])
            if (
                snapshot.next
                or state.get("route") != "refund_flow"
                or not any(
                    p.get("type") == "refund_form"
                    and p.get("draft", {}).get("order_id") == order_id
                    for p in proposals
                )
            ):
                raise ValueError("本轮没有该订单的退款申请，请先在聊天中确认。")
            if state.get("refund_ticket_no"):
                raise ValueError("退款工单已经提交，请不要重复提交。")
            ticket = await self.repository.create_ticket(
                cid, f"订单号：{order_id}\n退款原因：{reason}", "退款"
            )
            await graph.aupdate_state(config, {"refund_ticket_no": ticket.ticket_no}, as_node="log")
            return {"ticket_no": ticket.ticket_no, "ticket_type": "退款", "status": ticket.status}
