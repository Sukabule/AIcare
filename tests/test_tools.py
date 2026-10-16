import asyncio

from langchain_core.tools import tool

from app.tools.infra import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec, get_all_tools


def call(name, args, id="c1"):
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


async def test_six_tools_mock_reproducible_faq_ticket_and_injection(repo):
    class FakeRetrieval:
        async def search_knowledge(self, query, top_k):
            return [{"question": "退货政策", "answer": "7天无理由"}] if query == "退货政策" else []

    registry = ToolRegistry(repo, FakeRetrieval())
    assert set(registry.tools) == {
        "query_order",
        "query_product",
        "query_promotions",
        "query_faq",
        "create_ticket",
        "submit_refund",
    }
    ticket_tool = registry.tools["create_ticket"]
    assert "conversation_id" not in ticket_tool.tool_call_schema.model_json_schema()["properties"]
    assert len(await get_all_tools(registry)) == 9
    executor = ToolExecutor(registry)
    cid = (await repo.get_or_create("test", None)).id
    for name, args in [
        ("query_order", {"order_id": "1001"}),
        ("query_product", {"product_name": "猫粮"}),
        ("query_logistics", {"tracking_no": "SF1001"}),
    ]:
        a = await executor.execute(call(name, args), cid)
        b = await executor.execute(call(name, args), cid)
        assert a.ok and a.data == b.data
        assert a.data["source"] == "mock"
    assert (await executor.execute(call("query_faq", {"keyword": "退货政策"}), cid)).data["found"]
    missing = await executor.execute(call("query_faq", {"keyword": "邮费"}), cid)
    assert missing.ok and not missing.data["found"]
    result = await executor.execute(
        call(
            "create_ticket",
            {"description": "退款未到账", "ticket_type": "售后", "conversation_id": 99999},
        ),
        cid,
    )
    assert not result.ok  # injection attempts are rejected, never sent to the tool
    result = await executor.execute(
        call("create_ticket", {"description": "退款未到账", "ticket_type": "售后"}),
        cid,
        confirmed=True,
    )
    assert result.ok and result.data["conversation_id"] == cid


async def test_unknown_invalid_and_failed_tool_return_errors(repo):
    executor = ToolExecutor(ToolRegistry(repo))
    assert not (await executor.execute(call("nonexistent", {}), 1)).ok
    assert not (await executor.execute(call("query_order", {}), 1)).ok
    assert not (await executor.execute(call("query_order", {"order_id": 1001}), 1)).ok


async def test_read_retry_timeout_and_write_never_retry(repo):
    attempts = {"read": 0, "write": 0}

    @tool
    async def query_retry() -> dict:
        """Read-only test operation."""
        attempts["read"] += 1
        if attempts["read"] == 1:
            raise ConnectionError("private details")
        return {"value": 1}

    @tool
    async def create_ticket(description: str, ticket_type: str) -> dict:
        """Writing test operation."""
        attempts["write"] += 1
        raise ConnectionError("private details")

    @tool
    async def slow() -> dict:
        """Slow test operation."""
        await asyncio.sleep(1)
        return {}

    registry = ToolRegistry(repo)
    for t in [query_retry, create_ticket, slow]:
        registry.specs.pop(t.name, None)
        registry.register(ToolSpec.from_tool(t))
    executor = ToolExecutor(registry, timeout_seconds=0.1, read_retries=1)
    assert (await executor.execute(call("query_retry", {}), 1)).ok
    failed = await executor.execute(
        call("create_ticket", {"description": "x", "ticket_type": "售后"}), 1, confirmed=True
    )
    assert not failed.ok and attempts == {"read": 2, "write": 1}
    assert "private" not in failed.model_dump_json()
    assert (await executor.execute(call("slow", {}), 1)).error["code"] == "tool_timeout"
