"""Pin the installed LangGraph interrupt/resume stream shape without any model calls."""

import asyncio
import json
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict, total=False):
    order_id: str


async def smoke():
    effects = []

    async def fetch_order(state):
        selected = interrupt({"type": "select_order", "orders": [{"order_id": "1001"}]})
        effects.append(selected)
        return {"order_id": selected}

    builder = StateGraph(State)
    builder.add_node("fetch_order", fetch_order)
    builder.add_edge(START, "fetch_order")
    builder.add_edge("fetch_order", END)
    config = {"configurable": {"thread_id": "smoke-order"}}
    with tempfile.TemporaryDirectory() as directory:
        async with AsyncSqliteSaver.from_conn_string(
            str(Path(directory) / "smoke.sqlite")
        ) as saver:
            await saver.setup()
            graph = builder.compile(checkpointer=saver)
            events = [
                e async for e in graph.astream({}, config, stream_mode=["messages", "updates"])
            ]
            payload = next(
                value["__interrupt__"][0].value
                for mode, value in events
                if "__interrupt__" in value
            )
            assert payload["type"] == "select_order" and effects == []
            assert (await graph.aget_state(config)).next == ("fetch_order",)
            resumed = [
                e
                async for e in graph.astream(
                    Command(resume="1001"), config, stream_mode=["messages", "updates"]
                )
            ]
            assert effects == ["1001"]
            assert not (await graph.aget_state(config)).next
            result = await graph.ainvoke({}, {"configurable": {"thread_id": "smoke-json"}})
            assert result["__interrupt__"][0].value["type"] == "select_order"
            print(
                json.dumps(
                    {
                        "langgraph": version("langgraph"),
                        "interrupt_mode": events[0][0],
                        "payload": payload,
                        "resume_updates": resumed,
                        "side_effects_after_resume": effects,
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    asyncio.run(smoke())
