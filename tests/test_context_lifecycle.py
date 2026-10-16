import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.config import Settings
from app.graph.runtime import GraphRuntime
from app.schemas import ChatRequest
from app.tools.registry import ToolRegistry


async def test_completed_turn_schedules_without_waiting_and_keeps_checkpoint(repo, tmp_path):
    gate = asyncio.Event()

    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages, config=None):
            return AIMessage(content="订单1001需要核实签收情况")

    async def classifier(query):
        return "订单"

    async def resolver(query, history):
        return query

    async def summarize(old, dialog):
        await gate.wait()
        return "用户咨询订单1001，客服说明需要核实签收情况。"

    runtime = GraphRuntime(
        repo,
        Model(),
        SimpleNamespace(),
        ToolRegistry(repo),
        Settings(
            _env_file=None,
            model_context_window=50000,
            context_window_max_tokens=4,
            graph_checkpoint_path=tmp_path / "summary.sqlite",
        ),
        len,
        classifier=classifier,
        reference_resolver=resolver,
    )
    runtime.summarizer.summarize = summarize
    try:
        cid = None
        for query in ["订单1001", "先核实", "继续"]:
            result = await asyncio.wait_for(
                runtime.run_agent_turn(
                    ChatRequest(user_id="u1", message=query, conversation_id=cid)
                ),
                timeout=3,
            )
            cid = result.conversation_id
        assert len(runtime.summarizer.tasks) == 1
        conv = await repo.get_conversation(cid)
        assert conv.layer1_from_msg_id and not conv.summary
        snapshot = await runtime.graph.aget_state(runtime.config(cid))
        assert len(snapshot.values["messages"]) == 6
        assert (
            len(
                runtime.nodes.window(
                    snapshot.values["messages"],
                    {**snapshot.values, "layer1_from_msg_id": conv.layer1_from_msg_id},
                )
            )
            <= 4
        )
        gate.set()
        await asyncio.gather(*runtime.summarizer.tasks.values())
        conv = await repo.get_conversation(cid)
        assert "1001" in conv.summary
        assert conv.summary_upto_msg_id == conv.layer1_from_msg_id
        assert len(await repo.history(cid)) == 6
        assert len((await runtime.graph.aget_state(runtime.config(cid))).values["messages"]) == 6
        # Only a new turn reloads the finished background projection.
        await runtime.run_agent_turn(
            ChatRequest(user_id="u1", message="最初那个订单", conversation_id=cid)
        )
        assert (await runtime.graph.aget_state(runtime.config(cid))).values[
            "summary"
        ] == conv.summary
    finally:
        await runtime.close()


async def test_settlement_failure_does_not_fail_reply(application, monkeypatch):
    runtime = application.state.agent_factory()
    calls = []

    async def broken(*args):
        calls.append(args)
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(runtime.repository, "update_layer1_from", broken)
    from dataclasses import replace

    runtime.nodes.budget = replace(runtime.nodes.budget, layer1=0)
    try:
        result = await runtime.run_agent_turn(ChatRequest(user_id="u1", message="查订单1001"))
        result = await runtime.run_agent_turn(
            ChatRequest(user_id="u1", message="继续查", conversation_id=result.conversation_id)
        )
        assert result.answer and calls
    finally:
        await runtime.close()
