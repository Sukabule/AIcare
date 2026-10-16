from types import SimpleNamespace

from langchain_core.tools import tool

from app.tools.infra import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


async def test_oversized_tool_result_becomes_small_failure_without_retry():
    calls = []

    @tool
    async def big_result(keyword: str) -> dict:
        """Return a large fixture."""
        calls.append(keyword)
        return {"answer": "大结果" * 2000}

    registry = ToolRegistry(SimpleNamespace(), scan=False)
    registry.register(ToolSpec.from_tool(big_result))
    executor = ToolExecutor(
        registry,
        max_result_tokens=100,
        token_counter=lambda rows: sum(len(m.text) for m in rows),
    )
    result = await executor.execute(
        {"name": "big_result", "id": "c", "args": {"keyword": "政策"}}, 1
    )
    assert not result.ok and result.error["code"] == "tool_result_too_large"
    assert calls == ["政策"]
