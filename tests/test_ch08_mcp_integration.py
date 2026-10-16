"""Official SDK + adapter over real local HTTP; never calls an LLM."""

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.db.models import ToolAuditLog
from app.tools.engine import ToolEngine
from app.tools.mcp_client import MCPDiscovery
from app.tools.registry import ToolRegistry
from scripts.mcp_services import stop_process

pytestmark = pytest.mark.mcp_integration
ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_server(process, port):
    async with httpx.AsyncClient() as client:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("MCP subprocess exited")
            try:
                await client.get(f"http://127.0.0.1:{port}/mcp", timeout=0.3)
                return
            except httpx.TransportError:
                await asyncio.sleep(0.05)
    raise TimeoutError("MCP startup")


@pytest.fixture
async def servers(tmp_path):
    ports, processes, streams = [free_port(), free_port()], [], []
    script = tmp_path / "logistics.py"
    script.write_text(
        (ROOT / "mcp_servers/logistics_server.py").read_text("utf-8"), encoding="utf-8"
    )

    async def launch(index, path, delay="0"):
        stream = (tmp_path / f"server-{index}-{len(processes)}.log").open("wb")
        streams.append(stream)
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, str(path)],
            cwd=ROOT,
            env={**os.environ, "MCP_PORT": str(ports[index]), "MOCK_DELAY_SECONDS": delay},
            stdout=stream,
            stderr=stream,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append(process)
        process.mcp_script = path
        await wait_server(process, ports[index])
        return process

    try:
        logistics = await launch(0, script)
        await launch(1, ROOT / "mcp_servers/aftersales_server.py")
        settings = Settings(
            mcp_logistics_url=f"http://127.0.0.1:{ports[0]}/mcp",
            mcp_aftersales_url=f"http://127.0.0.1:{ports[1]}/mcp",
        )
        yield settings, logistics, script, launch
    finally:
        for process in processes:
            if process.poll() is None:
                await asyncio.to_thread(stop_process, process.pid, process.mcp_script)
            await asyncio.to_thread(process.wait, timeout=5)
        for stream in streams:
            stream.close()


async def test_three_tools_real_http_and_dynamic_addition_without_client_restart(servers):
    settings, process, script, launch = servers
    discovery = MCPDiscovery(settings)
    before = {s.name: s for s in await discovery.fetch_mcp_specs()}
    assert set(before) == {"query_logistics", "query_warranty", "query_return_status"}
    for name, spec in before.items():
        assert spec.description and spec.json_schema["properties"]
        args = {"tracking_no": "SF1001"} if name == "query_logistics" else {"order_id": "1001"}
        result = spec.format_result(await spec.tool.ainvoke(args))
        assert result["source"] == "mock"
        if name == "query_logistics":
            assert result["status"] == "运输中" and "status_code" not in result
    await asyncio.to_thread(stop_process, process.pid, script)
    await asyncio.to_thread(process.wait, timeout=5)
    content = script.read_text("utf-8")
    addition = (
        "@mcp.tool()\nasync def query_delivery_eta(tracking_no: str) -> dict:\n"
        '    """查询预计送达的演示数据。"""\n'
        '    return {"tracking_no": tracking_no, "eta": "预计明天"}\n\n'
    )
    script.write_text(
        content.replace('if __name__ == "__main__":', addition + 'if __name__ == "__main__":'),
        encoding="utf-8",
    )
    await launch(0, script)
    after = {s.name: s for s in await discovery.fetch_mcp_specs(refresh=True)}
    assert "query_delivery_eta" in after
    spec = after["query_delivery_eta"]
    assert (
        spec.format_result(await spec.tool.ainvoke({"tracking_no": "SF1001"}))["eta"] == "预计明天"
    )


async def test_real_mcp_delay_retries_audit_and_actual_ticket_delay_no_retry(servers, repo, db):
    from sqlalchemy import select

    settings, process, script, launch = servers
    await asyncio.to_thread(stop_process, process.pid, script)
    await asyncio.to_thread(process.wait, timeout=5)
    await launch(0, script, delay="0.5")
    settings = settings.model_copy(
        update={
            "mcp_tool_timeout": 0.1,
            "tool_default_timeout": 0.01,
            "demo_ticket_delay_seconds": 0.5,
        }
    )
    # Discovery has its own allowance: tool timeout is intentionally short in this test.
    discovery = MCPDiscovery(settings)
    discovery.timeout = 3
    registry = ToolRegistry(repo, settings=settings, mcp_client=discovery)
    engine = ToolEngine(registry, settings, backoff=0)
    cid = (await repo.get_or_create("timeouts", None)).id
    read = await engine.execute_tool_call(
        {"name": "query_logistics", "args": {"tracking_no": "SF1001"}, "id": "slow-read"}, cid
    )
    assert not read.ok and read.error["code"] == "tool_timeout"
    write = await engine.execute_tool_call(
        {
            "name": "create_ticket",
            "args": {"description": "需要人工跟进", "ticket_type": "售后"},
            "id": "slow-write",
        },
        cid,
        confirmed=True,
    )
    assert not write.ok and "不要" in write.error["message"]
    async with db.sessions() as session:
        audits = list(await session.scalars(select(ToolAuditLog).order_by(ToolAuditLog.id)))
        assert [(a.status, a.retry_count) for a in audits] == [("超时", 2), ("超时", 0)]
        assert audits[0].tool_source == "mcp" and audits[0].mcp_server == "logistics"
        assert audits[0].duration_ms >= 300
