import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from app.tools.engine import ToolEngine
from app.tools.registry import ToolRegistry, ToolSpec


class AuditRepo:
    def __init__(self):
        self.rows = []

    async def insert_tool_audit(self, **row):
        self.rows.append(row)


def setup(operation, *, timeout=0.2, retries=2, schema=None, formatter=None):
    repo = AuditRepo()
    registry = ToolRegistry(repo, scan=False)
    spec = ToolSpec.from_tool(operation, timeout=timeout, format_result=formatter)
    if schema:
        spec.json_schema = schema
    registry.register(spec)
    settings = SimpleNamespace(
        tool_default_timeout=timeout, mcp_tool_timeout=timeout, tool_max_retries=retries
    )
    return ToolEngine(registry, settings, backoff=0), repo


def call(name, args=None):
    return {"name": name, "args": {} if args is None else args, "id": "c1"}


@tool
async def query_test(value: int) -> dict:
    """只读测试。"""
    return {"中文": value}


@pytest.mark.parametrize(
    "args", [{}, {"value": "1"}, {"value": True}, {"value": 0}, {"value": 1, "extra": 2}]
)
async def test_invalid_arguments_are_audited_without_execution(args):
    engine, repo = setup(
        query_test,
        schema={
            "type": "object",
            "properties": {"value": {"type": "integer", "minimum": 1}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    result = await engine.execute_tool_call(call("query_test", args), 9)
    assert not result.ok and result.error["code"] == "invalid_arguments"
    assert repo.rows[0]["status"] == "校验拦下"
    assert repo.rows[0]["arguments"] == args and repo.rows[0]["retry_count"] == 0


async def test_unknown_tool_and_success_with_formatter_and_audit_failure():
    engine, repo = setup(query_test, formatter=lambda data: {"值": data["中文"]})
    result = await engine.execute_tool_call(call("query_test", {"value": 2}), 999)
    assert result.ok and result.data == {"值": 2}
    row = repo.rows[0]
    assert row["tool_source"] == "builtin" and row["conversation_id"] == 999
    assert "值" in row["result_summary"] and len(row["result_summary"]) <= 500
    assert row["duration_ms"] >= 0
    result = await engine.execute_tool_call(call("unknown"), 999)
    assert not result.ok and repo.rows[-1]["status"] == "失败"

    async def failed_audit(**row):
        raise ConnectionError("audit down")

    repo.insert_tool_audit = failed_audit
    assert (await engine.execute_tool_call(call("query_test", {"value": 3}), 9)).ok


async def test_network_retry_empty_and_business_failures():
    attempts = 0

    @tool
    async def query_retry() -> dict:
        """只读暂时故障。"""
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("secret")
        return {"hits": []}

    engine, repo = setup(query_retry)
    assert (await engine.execute_tool_call(call("query_retry"), 1)).ok
    assert attempts == 3 and repo.rows[0]["retry_count"] == 2
    await engine.execute_tool_call(call("query_retry"), 1)
    assert attempts == 4 and repo.rows[-1]["retry_count"] == 0

    @tool
    async def business_failure() -> dict:
        """非网络故障不重试。"""
        raise ValueError("private data")

    engine, repo = setup(business_failure)
    result = await engine.execute_tool_call(call("business_failure"), 1)
    assert not result.ok and repo.rows[0]["retry_count"] == 0
    assert "ValueError" in result.error["message"] and "private" not in str(result)


async def test_read_timeout_and_write_permission_never_retry():
    counts = {"read": 0, "write": 0}

    @tool
    async def read_slow() -> dict:
        """慢查询。"""
        counts["read"] += 1
        await asyncio.sleep(1)
        return {}

    @tool
    async def create_ticket() -> dict:
        """慢写操作。"""
        counts["write"] += 1
        await asyncio.sleep(1)
        return {}

    engine, repo = setup(read_slow, timeout=0.02)
    result = await engine.execute_tool_call(call("read_slow"), 1)
    assert not result.ok and counts["read"] == 3
    assert repo.rows[0]["status"] == "超时" and repo.rows[0]["retry_count"] == 2
    engine, repo = setup(create_ticket)
    for confirmed in (False, "true", 1):
        result = await engine.execute_tool_call(call("create_ticket"), 1, confirmed=confirmed)
        assert not result.ok and counts["write"] == 0
        assert repo.rows[-1]["status"] == "权限拒绝"
    result = await engine.execute_tool_call(call("create_ticket"), 1, confirmed=True)
    assert not result.ok and counts["write"] == 1
    assert "不要" in result.error["message"] and repo.rows[-1]["retry_count"] == 0


async def test_permission_cannot_be_overridden_by_spec():
    @tool
    async def create_ticket() -> dict:
        """写工具伪装成读。"""
        raise AssertionError("must not execute")

    engine, repo = setup(create_ticket)
    engine.registry.specs["create_ticket"].permission = "read"
    result = await engine.execute_tool_call(call("create_ticket"), 1)
    assert result.error["code"] == "permission_denied"
    assert repo.rows[-1]["status"] == "权限拒绝"


async def test_cancelled_call_is_audited_and_cancellation_propagates():
    entered = asyncio.Event()

    @tool
    async def waiting() -> dict:
        """查询中断。"""
        entered.set()
        await asyncio.sleep(5)
        return {}

    engine, repo = setup(waiting, timeout=10)
    task = asyncio.create_task(engine.execute_tool_call(call("waiting"), 1))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(repo.rows) == 1 and repo.rows[0]["status"] == "失败"


async def test_invalid_long_value_does_not_expand_error_beyond_tool_budget():
    engine, repo = setup(query_test)
    result = await engine.execute_tool_call(call("query_test", {"value": "超长" * 5000}), 1)
    assert not result.ok and len(result.model_dump_json()) < 500
    assert repo.rows[0]["status"] == "校验拦下"
