ROUTES = {
    "商品咨询": "knowledge",
    "退款退货": "refund_flow",
    "物流": "business",
    "订单": "business",
    "人工": "business",
    "售后": "refund_flow",
    "投诉": "escalate",
    "闲聊": "fallback_script",
    "其他": "fallback_script",
}


def route_by_intent(state):
    return ROUTES[state["intent"]]


def should_continue(state):
    if not state["messages"][-1].tool_calls:
        return "log"
    if state.get("proposal_stop") or state["steps"] >= state["max_agent_steps"]:
        return "fallback_reply"
    return "agent_tools"


def total_tokens(usage):
    return int((usage or {}).get("total_tokens", 0))
