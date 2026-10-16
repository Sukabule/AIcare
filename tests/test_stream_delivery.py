import asyncio

from langchain_core.messages import AIMessage, AIMessageChunk

from app.core.agent import AgentService
from app.schemas import ChatRequest
from app.tools.registry import ToolRegistry


async def test_first_delta_arrives_before_model_finishes_and_close_discards_final(repo, model):
    completed = False
    closed = False
    model.first = AIMessage(
        content="",
        tool_calls=[
            {"name": "query_order", "args": {"order_id": "1001"}, "id": "c1", "type": "tool_call"}
        ],
    )

    async def gated(messages):
        nonlocal completed, closed
        try:
            yield AIMessageChunk(content="first")
            await asyncio.Event().wait()
            completed = True
        finally:
            closed = True

    model.astream = gated
    service = AgentService(repo, model, ToolRegistry(repo), token_counter=len)
    cid = (await repo.get_or_create("u1", None)).id
    stream = service.stream_agent_turn(ChatRequest(user_id="u1", message="hi", conversation_id=cid))
    assert (await anext(stream))["event"] == "tool"
    assert await asyncio.wait_for(anext(stream), 2) == {"delta": "first"}
    assert not completed
    await stream.aclose()
    assert closed  # upstream stream must be closed immediately, not left for GC
    assert not service._locks
    assert [m.role for m in await repo.history(cid)] == ["user", "assistant", "tool"]
