from langgraph.graph import END, START, StateGraph

from app.graph.routing import route_by_intent, should_continue
from app.graph.state import ConversationState


def build_graph(nodes, checkpointer):
    graph = StateGraph(ConversationState)
    for name in (
        "resolve_reference",
        "classify_intent",
        "retrieve_knowledge",
        "fetch_order",
        "retrieve_policy",
        "confidence_check",
        "script_reply",
        "complaint_reply",
        "fallback_reply",
        "main_agent",
        "agent_tools",
        "log",
    ):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "resolve_reference")
    graph.add_edge("resolve_reference", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "knowledge": "retrieve_knowledge",
            "business": "main_agent",
            "refund_flow": "fetch_order",
            "fallback_script": "script_reply",
            "escalate": "complaint_reply",
        },
    )
    graph.add_edge("retrieve_knowledge", "confidence_check")
    graph.add_edge("fetch_order", "retrieve_policy")
    graph.add_edge("retrieve_policy", "confidence_check")
    graph.add_conditional_edges(
        "confidence_check", lambda s: "main_agent" if s["evidence_strong"] else "fallback_reply"
    )
    graph.add_conditional_edges("main_agent", should_continue)
    graph.add_conditional_edges(
        "agent_tools", lambda s: "main_agent" if s["evidence_strong"] else "log"
    )
    for name in ("script_reply", "complaint_reply", "fallback_reply"):
        graph.add_edge(name, "log")
    graph.add_edge("log", END)
    return graph.compile(checkpointer=checkpointer)
