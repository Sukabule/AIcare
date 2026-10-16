import json
from contextlib import contextmanager
from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import LowConfidenceQuestion, Ticket
from app.db.rag_repository import RagRepository
from app.graph.runtime import GraphRuntime, message_delta
from app.schemas import ChatRequest
from app.tools.registry import ToolRegistry


class SequenceModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        self.tools.append([t.name for t in tools])
        return self

    async def ainvoke(self, messages, config=None):
        self.inputs.append(messages)
        return self.replies.pop(0)


def call(name, args, ident):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


async def runtime(repo, db, tmp_path, intent, model, *, strong=True, max_steps=6):
    class Retrieval:
        async def search_knowledge(self, *args, **kwargs):
            return [
                {
                    "id": 1,
                    "question": "退货政策",
                    "answer": "7天无理由",
                    "section_path": "退货政策",
                    "score": 0.9,
                }
            ]

    async def assess(*args):
        return (None, "可回答") if strong else ("self_check", "证据不够")

    async def classifier(query):
        return intent

    rag = SimpleNamespace(
        retrieval=Retrieval(), assess=assess, repository=RagRepository(db.sessions)
    )
    settings = Settings(graph_checkpoint_path=tmp_path / "state.sqlite", max_agent_steps=max_steps)

    async def resolve(query, history):
        return query

    return GraphRuntime(
        repo, model, rag, ToolRegistry(repo), settings, len, classifier, reference_resolver=resolve
    )


async def test_runtime_records_request_and_final_answer_at_trace_boundary(
    repo, db, tmp_path, monkeypatch
):
    model = SequenceModel([AIMessage(content="订单查询完成")])
    graph = await runtime(repo, db, tmp_path, "订单", model)
    inputs, outputs = [], []

    @contextmanager
    def scope(cid, **kwargs):
        inputs.append(kwargs["input"])
        yield None

    monkeypatch.setattr(graph.observability, "request_scope", scope)
    monkeypatch.setattr(graph.observability, "record_output", outputs.append)
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="trace", message="查询订单1001"))
        assert inputs == [{"question": "查询订单1001", "conversation_id": result.conversation_id}]
        assert outputs == [result.answer] == ["订单查询完成"]
    finally:
        await graph.close()


async def test_knowledge_is_forced_before_agent_and_cannot_repeat_faq(repo, db, tmp_path):
    model = SequenceModel([AIMessage(content="7天无理由[1]")])
    graph = await runtime(repo, db, tmp_path, "商品咨询", model)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="退货政策"))
        assert r.trace["nodes"] == [
            "resolve_reference",
            "classify_intent",
            "retrieve_knowledge",
            "confidence_check",
            "main_agent",
            "log",
        ]
        assert "query_faq" not in model.tools[0]
        assert r.citations[0]["number"] == 1
    finally:
        await graph.close()


async def test_weak_evidence_short_circuits_before_model_and_pools_once(repo, db, tmp_path):
    model = SequenceModel([])
    graph = await runtime(repo, db, tmp_path, "商品咨询", model, strong=False)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="库外问题"))
        assert r.trace["fallback"] and not model.inputs
        async with db.sessions() as s:
            assert await s.scalar(select(func.count()).select_from(LowConfidenceQuestion)) == 1
    finally:
        await graph.close()


async def test_complaint_actions_do_not_write_and_do_not_enter_agent(repo, db, tmp_path):
    model = SequenceModel([])
    graph = await runtime(repo, db, tmp_path, "投诉", model)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="我要投诉"))
        assert [a["type"] for a in r.suggested_actions] == ["transfer_human", "create_ticket"]
        assert not model.inputs and "main_agent" not in r.trace["nodes"]
        async with db.sessions() as s:
            assert await s.scalar(select(func.count()).select_from(Ticket)) == 0
    finally:
        await graph.close()


async def test_ticket_call_pauses_until_confirmed_and_next_response_is_unbound(repo, db, tmp_path):
    model = SequenceModel(
        [
            call("create_ticket", {"description": "商品损坏", "ticket_type": "售后"}, "t"),
            AIMessage(content="请填写表单，尚未创建工单"),
        ]
    )
    graph = await runtime(repo, db, tmp_path, "订单", model)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="帮我创建工单"))
        assert r.interrupt["kind"] == "confirm_ticket" and not r.tool_results
        r = await graph.resume_turn(r.conversation_id, confirmed=False)
        assert not r.tool_results[0].ok and r.trace["steps"] == 2
        assert len(model.tools) == 1
        async with db.sessions() as s:
            assert await s.scalar(select(func.count()).select_from(Ticket)) == 0
    finally:
        await graph.close()


async def test_steps_limit_does_not_execute_or_return_tool_call(repo, db, tmp_path):
    model = SequenceModel([call("query_order", {"order_id": "1001"}, "c")])
    graph = await runtime(repo, db, tmp_path, "订单", model, max_steps=1)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="查订单"))
        assert r.trace["fallback"] and not r.tool_results
        assert "未能完成" in r.answer
    finally:
        await graph.close()


async def test_checkpoint_restores_across_runtime_and_turn_fields_reset(repo, db, tmp_path):
    model = SequenceModel([AIMessage(content="第一轮答复")])
    first = await runtime(repo, db, tmp_path, "订单", model)
    r = await first.run_agent_turn(ChatRequest(user_id="u", message="订单1001"))
    await first.close()
    next_model = SequenceModel([AIMessage(content="第二轮答复")])
    second = await runtime(repo, db, tmp_path, "订单", next_model)
    try:
        result = await second.run_agent_turn(
            ChatRequest(user_id="u", message="继续", conversation_id=r.conversation_id)
        )
        assert any(m.text == "第一轮答复" for m in next_model.inputs[0])
        assert result.trace["steps"] == 1 and result.tool_calls == []
    finally:
        await second.close()


def test_stream_filters_classifier_selfcheck_and_tool_arguments():
    token = AIMessageChunk(content='{"intent":"投诉"}')
    assert message_delta(token, {"langgraph_node": "classify_intent"}) is None
    assert message_delta(token, {"langgraph_node": "retrieve_knowledge"}) is None
    assert message_delta(AIMessageChunk(content="答复"), {"langgraph_node": "main_agent"}) == {
        "delta": "答复"
    }


async def test_real_graph_streams_before_completion_and_cancel_stops_node(repo, db, tmp_path):
    import asyncio
    from typing import Any

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGenerationChunk

    class GatedModel(BaseChatModel):
        gate: Any
        closed: bool = False

        @property
        def _llm_type(self):
            return "gated-graph-test"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise AssertionError("expected streaming callbacks")

        def bind_tools(self, tools):
            return self

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            try:
                yield ChatGenerationChunk(message=AIMessageChunk(content="first"))
                await self.gate.wait()
                yield ChatGenerationChunk(message=AIMessageChunk(content="second"))
            finally:
                self.closed = True

    model = GatedModel(gate=asyncio.Event())
    graph = await runtime(repo, db, tmp_path, "订单", model)
    cid = (await repo.get_or_create("u", None)).id
    stream = graph.stream_agent_turn(
        ChatRequest(user_id="u", message="查订单", conversation_id=cid)
    )
    try:
        assert await asyncio.wait_for(anext(stream), 3) == {"delta": "first"}
        assert not model.closed
        await stream.aclose()
        assert model.closed and not graph._locks
        assert [m.role for m in await repo.history(cid)] == ["user"]
    finally:
        model.gate.set()
        await stream.aclose()
        await graph.close()


async def test_invalid_ticket_injected_args_never_execute(repo, db, tmp_path):
    model = SequenceModel(
        [
            call(
                "create_ticket",
                {"description": "投诉", "ticket_type": "投诉", "conversation_id": 999},
                "c",
            ),
            AIMessage(content="请补充信息"),
        ]
    )
    graph = await runtime(repo, db, tmp_path, "订单", model)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="创建工单"))
        assert not r.tool_results[0].ok and not r.suggested_actions
        async with db.sessions() as s:
            assert await s.scalar(select(func.count()).select_from(Ticket)) == 0
    finally:
        await graph.close()


async def test_order_logistics_dependency_and_response_usage_accumulate(repo, db, tmp_path):
    from langchain_core.messages import ToolMessage

    class Chained(SequenceModel):
        async def ainvoke(self, messages, config=None):
            self.inputs.append(messages)
            if messages[-1].type == "human":
                ai = call("query_order", {"order_id": "1001"}, "o")
            elif isinstance(messages[-1], ToolMessage) and messages[-1].name == "query_order":
                tracking = json.loads(messages[-1].text)["data"]["tracking_no"]
                ai = call("query_logistics", {"tracking_no": tracking}, "l")
            else:
                ai = AIMessage(content="物流派送中（演示数据）")
            ai.usage_metadata = {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
            return ai

    model = Chained([])
    graph = await runtime(repo, db, tmp_path, "物流", model)
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="订单1001物流"))
        assert [c["name"] for c in r.tool_calls] == ["query_order", "query_logistics"]
        assert r.tool_calls[1]["args"]["tracking_no"] == r.tool_results[0].data["tracking_no"]
        assert r.trace["steps"] == 3 and r.trace["tokens_used"] == 39
        assert [m.role for m in await repo.history(r.conversation_id)] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
        ]
    finally:
        await graph.close()
