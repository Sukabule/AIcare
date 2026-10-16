"""First gate for a new upstream: real bind_tools smoke, no fallback."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.tools import tool  # noqa: E402

from app.config import Settings  # noqa: E402
from app.core.llm import create_chat_model  # noqa: E402


@tool
async def probe_order(order_id: str) -> dict:
    """Look up an order by its exact order ID."""
    return {"order_id": order_id}


async def main():
    model = create_chat_model(Settings())
    try:
        result = await model.bind_tools([probe_order]).ainvoke("请使用 probe_order 查询订单 1001。")
        print(result.tool_calls)
        return bool(
            len(result.tool_calls) == 1
            and result.tool_calls[0]["name"] == "probe_order"
            and result.tool_calls[0]["args"] == {"order_id": "1001"}
        )
    finally:
        await model.root_async_client.close()
        model.root_client.close()


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
    except Exception as exc:
        print("Smoke failed:", type(exc).__name__)
        success = False
    raise SystemExit(0 if success else 1)
