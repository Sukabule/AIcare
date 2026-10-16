import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.core.prompts import AGENT_SYSTEM


async def test_model_window_single_system_context_after_current_user(application, caplog):
    nodes = application.state.agent_factory().nodes
    state = {
        "conversation_id": 1,
        "question": "现在呢",
        "resolved_question": "订单1001现在呢",
        "summary": "订单1001请求退款",
        "summary_upto_msg_id": 2,
        "layer1_from_msg_id": 4,
        "route": "refund_flow",
        "order_data": {"order_id": "1001"},
        "evidence": "证据原文",
        "messages": [
            HumanMessage(content="被摘要的原文", id="db-1"),
            AIMessage(content="旧答复"),
            HumanMessage(content="中间层", id="db-3"),
            AIMessage(content="长答复" * 100),
            HumanMessage(content="现在呢", id="db-5"),
            AIMessage(content="", tool_calls=[{"name": "query_order", "args": {}, "id": "c"}]),
            ToolMessage(content="已发货", tool_call_id="c"),
        ],
    }
    with caplog.at_level(logging.INFO, logger="app.context"):
        messages = nodes.model_messages(state)
    assert sum(isinstance(m, SystemMessage) for m in messages) == 1
    assert messages[0].content == AGENT_SYSTEM
    assert "被摘要的原文" not in str(messages)
    assert messages[2].content.endswith("…(略)")
    assert messages[3].content == "现在呢"
    assert "订单1001请求退款" in messages[4].content and "证据原文" in messages[4].content
    assert messages[5].tool_calls[0]["id"] == messages[6].tool_call_id
    assert "model_ctx" in caplog.text and "订单1001请求退款" in caplog.text
    history = nodes.recent_history(state)
    assert "订单1001请求退款" in history[0].content
    assert "被摘要的原文" not in str(history)
    assert len(state["messages"]) == 7


async def test_runtime_loads_db_anchors_and_full_checkpoint(client, application, repo):
    first = await client.post("/api/agent", json={"user_id": "u1", "message": "查询订单1001"})
    assert first.status_code == 200
    cid = first.json()["conversation_id"]
    rows = await repo.history(cid)
    runtime = application.state.agent_factory()
    snapshot = await runtime.graph.aget_state(runtime.config(cid))
    assert snapshot.values["messages"][0].id == f"db-{rows[0].id}"
    await repo.update_conversation_summary(cid, "最初订单1001", rows[-1].id)
    from app.schemas.chat import ChatRequest

    await runtime.run_agent_turn(ChatRequest(user_id="u1", message="继续", conversation_id=cid))
    snapshot = await runtime.graph.aget_state(runtime.config(cid))
    assert len(snapshot.values["messages"]) >= 4
    assert snapshot.values["summary"] == "最初订单1001"
    assert snapshot.values["summary_upto_msg_id"] == rows[-1].id
    await runtime.close()
