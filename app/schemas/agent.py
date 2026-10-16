from typing import Any

from pydantic import BaseModel, Field

from app.schemas.chat import ChatRequest

AgentRequest = ChatRequest


class ToolResult(BaseModel):
    name: str
    tool_call_id: str
    ok: bool
    data: Any = None
    error: dict[str, str] | None = None


class AgentResult(BaseModel):
    conversation_id: int
    answer: str
    tool_calls: list[dict] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)

    suggested_actions: list[dict] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)
    interrupt: dict | None = None
