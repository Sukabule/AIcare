import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from app.tools.mcp_client import MCPDiscovery, format_logistics
from app.tools.registry import ToolRegistry, ToolSpec


@tool
async def query_external(value: str) -> dict:
    """外部只读工具。"""
    return {"value": value}


async def test_scan_drop_file_without_core_edit_and_duplicate_policy(tmp_path, caplog):
    package = tmp_path / "plugins_for_test"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "new_plugin.py").write_text(
        "from app.tools.registry import ToolSpec\n"
        "from langchain_core.tools import tool\n"
        "@tool\nasync def just_added(value: str) -> dict:\n"
        '    """新工具。"""\n    return {"value": value}\n'
        "def register(registry):\n    registry.register(ToolSpec.from_tool(just_added))\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        registry = ToolRegistry(None, package="plugins_for_test")
        assert list(registry.tools) == ["just_added"]
        spec = (await registry.get_all_specs())[0]
        assert spec.name and spec.description and spec.json_schema["required"] == ["value"]
        duplicate = ToolSpec.from_tool(spec.tool, source="mcp", mcp_server="external")

        class Discovery:
            async def fetch_mcp_specs(self):
                return [duplicate]

        registry.mcp_client = Discovery()
        assert (await registry.get_all_specs())[0].source == "builtin"
        assert "duplicate" in caplog.text
    finally:
        sys.path.remove(str(tmp_path))
        for name in list(sys.modules):
            if name.startswith("plugins_for_test"):
                del sys.modules[name]


async def test_mcp_discovery_is_fresh_server_failures_isolated(caplog):
    settings = SimpleNamespace(
        mcp_logistics_url="http://127.0.0.1:8101/mcp",
        mcp_aftersales_url="http://127.0.0.1:8102/mcp",
        mcp_tool_timeout=1,
        mcp_discovery_ttl_seconds=0,
        mcp_discovery_failure_ttl_seconds=0,
    )
    discovery = MCPDiscovery(settings)
    calls = []

    async def tools(*, server_name):
        calls.append(server_name)
        if server_name == "logistics":
            raise ConnectionError("down")
        return [query_external]

    discovery.client.get_tools = tools
    registry = ToolRegistry(None, mcp_client=discovery)
    for _ in range(2):
        specs = {s.name: s for s in await registry.get_all_specs()}
        assert specs["query_external"].mcp_server == "aftersales"
        assert specs["query_external"].permission == "read"
    assert calls.count("aftersales") == 2 and calls.count("logistics") == 2
    assert "skipped" in caplog.text
    assert "query_logistics" not in registry.tools  # builtin retired


def test_client_translates_internal_status_and_selects_fields():
    content = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "tracking_no": "SF1",
                    "status_code": "TRANSIT",
                    "internal_secret": "hidden",
                    "source": "mock",
                }
            ),
        }
    ]
    assert format_logistics(content) == {"tracking_no": "SF1", "status": "运输中", "source": "mock"}


def cached_discovery(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.tools.mcp_client.monotonic", lambda: clock[0])
    discovery = MCPDiscovery(
        SimpleNamespace(
            mcp_logistics_url="http://localhost:8101/mcp",
            mcp_aftersales_url="http://localhost:8102/mcp",
            mcp_tool_timeout=1,
            mcp_discovery_ttl_seconds=60,
            mcp_discovery_failure_ttl_seconds=5,
        )
    )
    return discovery, clock


async def test_catalog_concurrent_calls_share_discovery_and_expire(monkeypatch):
    discovery, clock = cached_discovery(monkeypatch)
    calls = []

    async def tools(*, server_name):
        calls.append(server_name)
        await asyncio.sleep(0)
        return [query_external]

    discovery.client.get_tools = tools
    results = await asyncio.gather(*(discovery.fetch_mcp_specs() for _ in range(12)))
    assert all(len(result) == 2 for result in results)
    assert sorted(calls) == ["aftersales", "logistics"]
    # Returned containers are independent; callers cannot clear the catalog.
    results[0].clear()
    assert len(await discovery.fetch_mcp_specs()) == 2
    clock[0] += 60
    await discovery.fetch_mcp_specs()
    assert len(calls) == 4


async def test_failed_server_recovers_without_refreshing_healthy_server(monkeypatch):
    discovery, clock = cached_discovery(monkeypatch)
    calls, down = [], [True]

    async def tools(*, server_name):
        calls.append(server_name)
        if server_name == "logistics" and down[0]:
            raise ConnectionError("offline")
        return [query_external]

    discovery.client.get_tools = tools
    assert len(await discovery.fetch_mcp_specs()) == 1
    down[0] = False
    clock[0] += 4
    assert len(await discovery.fetch_mcp_specs()) == 1
    clock[0] += 1
    assert len(await discovery.fetch_mcp_specs()) == 2
    assert calls.count("logistics") == 2
    assert calls.count("aftersales") == 1


async def test_refresh_replaces_stale_catalog_including_empty_and_failure(monkeypatch):
    discovery, clock = cached_discovery(monkeypatch)
    values = [[query_external]]
    calls = []

    async def tools(*, server_name):
        calls.append(server_name)
        if isinstance(values[0], Exception):
            raise values[0]
        return values[0]

    discovery.client.get_tools = tools
    assert len(await discovery.fetch_mcp_specs()) == 2
    values[0] = []
    assert await discovery.fetch_mcp_specs(refresh=True) == []
    assert await discovery.fetch_mcp_specs() == []
    assert len(calls) == 4
    values[0] = [query_external]
    assert len(await discovery.fetch_mcp_specs(refresh=True)) == 2
    values[0] = ConnectionError("down")
    clock[0] += 60
    assert await discovery.fetch_mcp_specs() == []
    assert await discovery.fetch_mcp_specs() == []
    assert len(calls) == 8


async def test_cancelled_discovery_does_not_poison_cache_or_lock(monkeypatch):
    discovery, _ = cached_discovery(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def tools(*, server_name):
        entered.set()
        await release.wait()
        return [query_external]

    discovery.client.get_tools = tools
    request = asyncio.create_task(discovery.fetch_mcp_specs())
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release.set()
    assert len(await asyncio.wait_for(discovery.fetch_mcp_specs(), 1)) == 2
