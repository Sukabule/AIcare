from langchain_core.messages import AIMessage

from app.graph.routing import route_by_intent, should_continue, total_tokens


def test_eight_intents_have_exact_five_routes():
    expected = {
        "商品咨询": "knowledge",
        "退款退货": "refund_flow",
        "物流": "business",
        "订单": "business",
        "售后": "refund_flow",
        "投诉": "escalate",
        "闲聊": "fallback_script",
        "其他": "fallback_script",
    }
    assert {k: route_by_intent({"intent": k}) for k in expected} == expected


def test_stop_before_executing_calls_at_budget_and_after_ticket_proposal():
    ai = AIMessage(
        content="", tool_calls=[{"name": "query_order", "args": {"order_id": "1001"}, "id": "c"}]
    )
    assert should_continue({"messages": [ai], "steps": 1, "max_agent_steps": 6}) == "agent_tools"
    assert should_continue({"messages": [ai], "steps": 6, "max_agent_steps": 6}) == "fallback_reply"
    assert (
        should_continue({"messages": [AIMessage(content="答复")], "steps": 6, "max_agent_steps": 6})
        == "log"
    )
    assert (
        should_continue({"messages": [ai], "steps": 2, "max_agent_steps": 6, "proposal_stop": True})
        == "fallback_reply"
    )


def test_usage_sum_ignores_missing_metadata():
    assert total_tokens(None) == 0
    assert total_tokens({"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}) == 13
