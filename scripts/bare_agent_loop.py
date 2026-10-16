"""Bare bounded ReAct loop for comparison with the production workflow graph."""

# ruff: noqa: E402
import argparse
import asyncio
import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings
from app.core.llm import create_chat_model
from app.core.prompts import GRAPH_AGENT_SYSTEM
from app.db.base import Database
from app.db.repository import Repository
from app.tools.infra import ToolExecutor
from app.tools.registry import ToolRegistry


async def main(question):
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    model = create_chat_model(settings)
    model.extra_body = {"enable_thinking": False}
    registry = ToolRegistry(Repository(db.sessions))
    # This demonstration only queries data; ticket creation needs the browser action.
    bound = model.bind_tools(
        [s.tool for s in await registry.get_all_specs() if s.name != "create_ticket"]
    )
    executor = ToolExecutor(registry)
    messages = [SystemMessage(content=GRAPH_AGENT_SYSTEM), HumanMessage(content=question)]
    try:
        for step in range(settings.max_agent_steps):
            response = await bound.ainvoke(messages)
            messages.append(response)
            print(f"step {step + 1}: {response.tool_calls or response.text}", flush=True)
            if not response.tool_calls:
                return
            if step + 1 == settings.max_agent_steps:
                print("到达步数上限，停止查询。")
                return
            for call in response.tool_calls:
                result = await executor.execute(call, 0, question)
                messages.append(
                    ToolMessage(
                        content=json.dumps(result.model_dump(), ensure_ascii=False),
                        tool_call_id=call["id"],
                    )
                )
    finally:
        await db.close()
        await model.root_async_client.close()
        model.root_client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default="订单1001的物流到哪了")
    asyncio.run(main(parser.parse_args().question))
