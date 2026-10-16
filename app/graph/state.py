from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ConversationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    conversation_id: int
    summary: str
    summary_upto_msg_id: int
    layer1_from_msg_id: int
    question: str
    resolved_question: str
    resolved_query: str
    intent_confidence: float
    order_id: str
    order_data: dict
    expanded_queries: list[str]
    refund_ticket_no: str
    intent: str
    route: str
    evidence: str
    citations: list[dict]
    evidence_strong: bool
    evidence_confidence: float
    fallback_source: str
    retrieved_snapshot: list[dict]
    gate_source: str
    gate_reason: str
    answer: str
    steps: int
    max_agent_steps: int
    tokens_used: int
    suggested_actions: list[dict]
    tool_calls: list[dict]
    tool_results: list[dict]
    proposal_stop: bool
    trace: dict
