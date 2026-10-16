import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.tools import tool

from app.core.agent import AgentService
from app.db.repository import ConversationNotFound
from app.schemas import ChatRequest
from app.tools.registry import ToolRegistry, ToolSpec


def request(message="订单 1001 的物流", conversation_id=None):
    return ChatRequest(user_id="u1", message=message, conversation_id=conversation_id)


def tools_reply():
    return AIMessage(
        content="我来查一下",
        tool_calls=[
            {
                "name": "query_logistics",
                "args": {"tracking_no": "SF1001"},
                "id": "log1",
                "type": "tool_call",
            }
        ],
    )


def service(repo, model):
    return AgentService(repo, model, ToolRegistry(repo), token_counter=len)


async def test_no_tool_answer_saved_once(repo, model):
    result = await service(repo, model).run_agent_turn(request("你好"))
    assert result.answer == model.first.content and not result.tool_calls
    assert [m.role for m in await repo.history(result.conversation_id)] == ["user", "assistant"]
    assert [kind for kind, _ in model.calls] == ["bound"]


async def test_tools_shared_core_order_and_single_round(repo, model):
    model.first = tools_reply()
    result = await service(repo, model).run_agent_turn(request())
    assert result.tool_calls[0]["name"] == "query_logistics"
    assert result.tool_results[0].ok
    rows = await repo.history(result.conversation_id)
    assert [m.role for m in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1].tool_calls and rows[2].tool_call_id == "log1"
    assert model.bindings == 1
    assert [kind for kind, _ in model.calls] == ["bound", "final"]
    assert isinstance(model.calls[1][1][-1], ToolMessage)


async def test_continuation_replays_final_only_and_ignores_failed_user(repo, model):
    agent = service(repo, model)
    model.first = tools_reply()
    first = await agent.run_agent_turn(request())
    await repo.append(first.conversation_id, "user", "中断了的请求")
    model.first = AIMessage(content="续接答复")
    await agent.run_agent_turn(request("再说一次", first.conversation_id))
    messages = model.calls[-1][1]
    assert [m.type for m in messages] == ["system", "human", "ai", "human"]
    assert messages[2].content == "工具核验后的答复"
    assert all(m.content != "我来查一下" for m in messages)


async def test_unknown_conversation_never_calls_model(repo, model):
    with pytest.raises(ConversationNotFound):
        await service(repo, model).run_agent_turn(request(conversation_id=9999999))
    assert not model.calls


async def test_stream_tool_before_execution_then_deltas_done_and_persistence(repo, model):
    model.first = tools_reply()
    agent = service(repo, model)
    stream = agent.stream_agent_turn(request())
    assert await anext(stream) == {"event": "tool", "name": "query_logistics"}
    assert not any(kind == "stream" for kind, _ in model.calls)
    events = [event async for event in stream]
    assert events[:-1] == [{"delta": "已查到"}, {"delta": "物流\n结果"}]
    assert events[-1]["event"] == "done"
    rows = await repo.history(events[-1]["conversation_id"])
    assert [m.role for m in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[-1].content == "已查到物流\n结果" and model.bindings == 1


async def test_stream_no_tools_single_block(repo, model):
    events = [event async for event in service(repo, model).stream_agent_turn(request("你好"))]
    assert events[0] == {"delta": model.first.content}
    assert events[1]["event"] == "done" and len(events) == 2


async def test_tool_error_goes_back_to_model_without_agent_loop(repo, model):
    model.first = AIMessage(
        content="", tool_calls=[{"name": "unknown", "args": {}, "id": "bad1", "type": "tool_call"}]
    )
    result = await service(repo, model).run_agent_turn(request())
    assert not result.tool_results[0].ok
    assert '"ok": false' in model.calls[-1][1][-1].content
    assert len(model.calls) == 2


async def test_stream_cancel_keeps_audit_but_no_partial_final(repo, model):
    model.first = tools_reply()

    async def interrupted(messages):
        yield AIMessageChunk(content="半句")
        raise asyncio.CancelledError()

    model.astream = interrupted
    agent = service(repo, model)
    cid = (await repo.get_or_create("u1", None)).id
    stream = agent.stream_agent_turn(request(conversation_id=cid))
    await anext(stream)
    assert await anext(stream) == {"delta": "半句"}
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)
    assert [m.role for m in await repo.history(cid)] == ["user", "assistant", "tool"]


async def test_parallel_calls_start_together_and_results_keep_call_order(repo, model):
    started = []
    both_started = asyncio.Event()

    @tool
    async def query_parallel(value: str) -> dict:
        """Test independent reads starting concurrently."""
        started.append(value)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 2)
        return {"value": value}

    agent = service(repo, model)
    agent.registry.register(ToolSpec.from_tool(query_parallel))
    model.first = AIMessage(
        content="",
        tool_calls=[
            {"name": "query_parallel", "args": {"value": value}, "id": value, "type": "tool_call"}
            for value in ["first", "second"]
        ],
    )
    result = await agent.run_agent_turn(request())
    assert len(started) == 2
    assert [r.data["value"] for r in result.tool_results] == ["first", "second"]
    assert [row.role for row in await repo.history(result.conversation_id)] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]


async def test_same_conversation_waits_until_final_answer_is_saved(repo, model):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def gated(messages):
        entered.set()
        await release.wait()
        return AIMessage(content="先完成的最终回答")

    model.first = tools_reply()
    model.ainvoke = gated
    agent = service(repo, model)
    cid = (await repo.get_or_create("u1", None)).id
    first = asyncio.create_task(agent.run_agent_turn(request(conversation_id=cid)))
    await asyncio.wait_for(entered.wait(), 2)
    model.first = AIMessage(content="第二轮回复")
    second = asyncio.create_task(agent.run_agent_turn(request("第二轮问题", cid)))
    await asyncio.sleep(0.05)
    assert not second.done()
    assert [row.role for row in await repo.history(cid)] == ["user", "assistant", "tool"]
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 3)
    assert model.calls[-1][1][2].content == "先完成的最终回答"
    assert not agent._locks


async def test_unexpected_second_tool_call_is_rejected_without_execution(repo, model):
    model.first = tools_reply()
    model.final = tools_reply()
    with pytest.raises(ValueError, match="Unexpected second"):
        await service(repo, model).run_agent_turn(request())
    assert model.bindings == 1
    assert [kind for kind, _ in model.calls] == ["bound", "final"]
