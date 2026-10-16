from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Ticket
from app.db.rag_repository import RagRepository
from app.graph.runtime import GraphRuntime
from app.schemas import ChatRequest
from app.tools.business import list_user_orders
from app.tools.registry import ToolRegistry


class Model:
    def __init__(self, replies):
        self.replies = list(replies)
        self.bindings = []
        self.inputs = []

    def bind_tools(self, tools):
        self.bindings.append([t.name for t in tools])
        return self

    async def ainvoke(self, messages, config=None):
        self.inputs.append(messages)
        return self.replies.pop(0)


async def test_stale_card_cannot_resume_new_pause(repo, db, tmp_path):
    graph, _, _ = await fixture_runtime(repo, db, tmp_path, [AIMessage(content="继续")])
    try:
        first = await graph.run_agent_turn(ChatRequest(user_id="u", message="我要退款"))
        second = await graph.run_agent_turn(
            ChatRequest(
                user_id="u", message="另一单申请退款", conversation_id=first.conversation_id
            )
        )
        assert first.interrupt["interrupt_id"] != second.interrupt["interrupt_id"]
        with pytest.raises(ValueError):
            await graph.resume_turn(first.conversation_id, "1001", first.interrupt["interrupt_id"])
        result = await graph.resume_turn(
            first.conversation_id, "1001", second.interrupt["interrupt_id"]
        )
        assert result.answer == "继续"
    finally:
        await graph.close()


async def test_refund_gate_precedes_agent(repo, db, tmp_path):
    graph, model, _ = await fixture_runtime(repo, db, tmp_path, [])

    async def weak(query, hits, **kwargs):
        return "self_check", "没有足够退款政策"

    graph.nodes.rag.assess = weak
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="u", message="订单1001能退吗"))
        assert result.trace["fallback"] and not model.inputs
        assert "main_agent" not in result.trace["nodes"] and result.trace["steps"] == 0
        assert not any(a["type"] == "refund_form" for a in result.suggested_actions)
    finally:
        await graph.close()


async def test_another_order_does_not_use_model_guessed_old_number(repo, db, tmp_path):
    graph, model, searches = await fixture_runtime(repo, db, tmp_path, [])

    async def incorrect_resolver(query, history):
        return "另一单订单1001能退吗"

    graph.nodes.reference_resolver = incorrect_resolver
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="u", message="另一单能退吗"))
        assert result.interrupt and not searches and not model.inputs
        assert [m.role for m in await repo.history(result.conversation_id)] == ["user"]
    finally:
        await graph.close()


async def test_intent_drift_resolves_history_and_clears_refund_fields(repo, db, tmp_path):
    graph, model, searches = await fixture_runtime(
        repo,
        db,
        tmp_path,
        [
            AIMessage(content="订单1001物流派送中"),
            AIMessage(content="订单1001退款需核实"),
            AIMessage(content="订单1001继续查询物流"),
        ],
        intent="物流",
    )
    seen = []

    async def resolver(query, history):
        seen.append([m.text for m in history])
        return (
            query if "1001" in query else ("订单1001能退吗" if "退" in query else "订单1001的物流")
        )

    async def classify(query):
        return {"intent": "退款退货" if "退" in query else "物流", "confidence": 0.9}

    graph.nodes.reference_resolver, graph.nodes.classifier = resolver, classify
    try:
        first = await graph.run_agent_turn(ChatRequest(user_id="u", message="订单1001物流"))
        second = await graph.run_agent_turn(
            ChatRequest(user_id="u", message="这个能退吗", conversation_id=first.conversation_id)
        )
        third = await graph.run_agent_turn(
            ChatRequest(
                user_id="u", message="还是查它的物流", conversation_id=first.conversation_id
            )
        )
        assert [r.trace["intent"] for r in (first, second, third)] == ["物流", "退款退货", "物流"]
        assert "订单1001物流派送中" in seen[1]
        assert second.trace["order_id"] == "1001"
        snapshot = await graph.graph.aget_state(graph.config(first.conversation_id))
        assert snapshot.values["order_id"] == "" and snapshot.values["order_data"] == {}
        assert snapshot.values["expanded_queries"] == [] and third.citations == []
        assert len(searches) == 3 and third.trace["steps"] == 1
        # Ch07 retains paired historical tool traces in tier 1; the new turn
        # must still reset refund data and avoid replaying old tool execution.
        current = max(
            i
            for i, m in enumerate(model.inputs[-1])
            if m.type == "human" and m.text == "还是查它的物流"
        )
        assert all(not getattr(m, "tool_calls", None) for m in model.inputs[-1][current:])
    finally:
        await graph.close()


async def fixture_runtime(repo, db, tmp_path, replies, *, intent="退款退货"):
    async def classifier(query):
        return {"intent": intent, "confidence": 0.9}

    async def resolver(query, history):
        return query

    async def expander(query, order):
        return ["退货条件", "未收货退款", "退款流程"]

    searches = []

    class Retrieval:
        async def search_knowledge(self, query, *args, **kwargs):
            if intent in ("退款退货", "售后"):
                assert kwargs.get("apply_understanding") is False
            searches.append(query)
            return [{"id": 1, "question": "退货", "answer": "7天可申请", "score": 0.9}]

    async def assess(query, hits, **kwargs):
        assert len(hits) == 1
        return None, "证据足够"

    rag = SimpleNamespace(
        retrieval=Retrieval(), assess=assess, repository=RagRepository(db.sessions)
    )
    model = Model(replies)
    graph = GraphRuntime(
        repo,
        model,
        rag,
        ToolRegistry(repo),
        Settings(graph_checkpoint_path=tmp_path / "state.sqlite"),
        len,
        classifier,
        reference_resolver=resolver,
        expander=expander,
    )
    return graph, model, searches


async def test_missing_order_interrupts_and_resume_executes_once(repo, db, tmp_path):
    graph, model, searches = await fixture_runtime(
        repo, db, tmp_path, [AIMessage(content="可申请退货[1]")]
    )
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="u", message="我要申请退款"))
        assert result.interrupt["kind"] == "select_order" and not searches and not model.bindings
        assert [m.role for m in await repo.history(result.conversation_id)] == ["user"]
        order = result.interrupt["orders"][0]["order_id"]
        resumed = await graph.resume_turn(result.conversation_id, order)
        assert resumed.interrupt is None and resumed.trace["route"] == "refund_flow"
        assert len(searches) == 3 and len(resumed.citations) == 1
        assert [c["name"] for c in resumed.tool_calls].count("query_order") == 1
        assert "query_faq" not in model.bindings[0] and "query_order" not in model.bindings[0]
        assert resumed.trace["order_id"] == order
        with pytest.raises(ValueError):
            await graph.resume_turn(result.conversation_id, order)
    finally:
        await graph.close()


async def test_invalid_resume_keeps_checkpoint_pending(repo, db, tmp_path):
    graph, _, _ = await fixture_runtime(repo, db, tmp_path, [AIMessage(content="可申请")])
    try:
        r = await graph.run_agent_turn(ChatRequest(user_id="u", message="我要退款"))
        with pytest.raises(ValueError):
            await graph.resume_turn(r.conversation_id, "not-offered")
        resumed = await graph.resume_turn(r.conversation_id, r.interrupt["orders"][0]["order_id"])
        assert resumed.answer == "可申请"
    finally:
        await graph.close()


async def test_pending_order_survives_runtime_restart(repo, db, tmp_path):
    first, _, _ = await fixture_runtime(repo, db, tmp_path, [])
    r = await first.run_agent_turn(ChatRequest(user_id="u", message="我要退款"))
    await first.close()
    second, _, _ = await fixture_runtime(repo, db, tmp_path, [AIMessage(content="继续完成")])
    try:
        result = await second.resume_turn(r.conversation_id, r.interrupt["orders"][0]["order_id"])
        assert result.answer == "继续完成" and result.trace["steps"] == 1
    finally:
        await second.close()


async def test_stream_interrupt_and_resumed_events(repo, db, tmp_path):
    graph, _, _ = await fixture_runtime(
        repo, db, tmp_path, [AIMessage(content="不能退，请联系人工核实")]
    )
    try:
        events = [
            e async for e in graph.stream_agent_turn(ChatRequest(user_id="u", message="我要退款"))
        ]
        stop = next(e for e in events if e.get("event") == "interrupt")
        assert not any(e.get("event") == "done" for e in events)
        resumed = [
            e
            async for e in graph.stream_resume_turn(
                stop["conversation_id"], stop["orders"][0]["order_id"]
            )
        ]
        assert resumed[-1] == {"event": "done", "conversation_id": stop["conversation_id"]}
        assert not any(e.get("event") == "actions" for e in resumed)
    finally:
        await graph.close()


async def test_submit_refund_is_proposal_and_not_database_write(repo, db, tmp_path):
    order = list_user_orders("u")[0]["order_id"]
    ai = AIMessage(
        content="", tool_calls=[{"name": "submit_refund", "args": {"order_id": order}, "id": "r"}]
    )
    graph, model, _ = await fixture_runtime(
        repo, db, tmp_path, [ai, AIMessage(content="请提交退款申请表")]
    )
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="u", message=f"订单{order}能退吗"))
        assert result.suggested_actions == [{"type": "refund_form", "draft": {"order_id": order}}]
        assert result.tool_results[-1].data["proposed"]
        assert len(model.bindings) == 1
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Ticket)) == 0
    finally:
        await graph.close()
