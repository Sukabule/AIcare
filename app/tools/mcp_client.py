import asyncio
import json
import logging
from time import monotonic

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


def normalize_result(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        texts = [
            item["text"] for item in value if isinstance(item, dict) and item.get("type") == "text"
        ]
        if len(texts) == 1:
            parsed = json.loads(texts[0])
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("MCP 工具没有返回可识别的 JSON 对象")


def format_logistics(value):
    data = normalize_result(value)
    return {
        key: data[key] for key in ("source", "tracking_no", "location", "timeline") if key in data
    } | {
        "status": {"TRANSIT": "运输中", "DELIVERING": "派送中", "DELIVERED": "已签收"}.get(
            data.get("status_code"), "状态待核实"
        )
    }


class MCPDiscovery:
    def __init__(self, settings):
        urls = {"logistics": settings.mcp_logistics_url, "aftersales": settings.mcp_aftersales_url}
        self.client = MultiServerMCPClient(
            {
                name: {"transport": "streamable_http", "url": url}
                for name, url in urls.items()
                if url
            },
            handle_tool_errors=False,
        )
        self.timeout = min(settings.mcp_tool_timeout, 3.0)

        self.cache_ttl = getattr(settings, "mcp_discovery_ttl_seconds", 60.0)
        self.failure_ttl = getattr(settings, "mcp_discovery_failure_ttl_seconds", 5.0)
        self._cache = {}
        self._locks = {server: asyncio.Lock() for server in self.client.connections}

    async def fetch_mcp_specs(self, *, refresh=False):
        """Cache definitions, never execution results or live MCP sessions.

        A successful empty catalog is cached too. Failed refreshes discard old
        definitions and briefly cache an empty catalog; other servers stay usable.
        refresh=True explicitly reloads catalogs (e.g. after server tool changes).
        """
        groups = await asyncio.gather(
            *(self._discover(server, refresh=refresh) for server in self.client.connections)
        )
        return [spec for group in groups for spec in group]

    async def _discover(self, server, *, refresh):
        async with self._locks[server]:
            cached = self._cache.get(server)
            if not refresh and cached is not None and monotonic() < cached[0]:
                return cached[1]
            try:
                tools = await asyncio.wait_for(
                    self.client.get_tools(server_name=server), self.timeout
                )
                specs = [
                    ToolSpec.from_tool(
                        t,
                        source="mcp",
                        mcp_server=server,
                        format_result=format_logistics
                        if t.name == "query_logistics"
                        else normalize_result,
                    )
                    for t in tools
                ]
                ttl = self.cache_ttl
            except Exception as exc:
                logger.warning("MCP discovery skipped: %s (%s)", server, type(exc).__name__)
                specs, ttl = [], self.failure_ttl
            self._cache[server] = (monotonic() + ttl, specs)
            return specs
