"""Process-local builtins merged with the MCP client's expiring catalog."""

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from langchain_core.tools import BaseTool

from app.config import Settings

logger = logging.getLogger(__name__)
WRITE_TOOLS = {"create_ticket"}


@dataclass
class ToolSpec:
    name: str
    description: str
    json_schema: dict
    tool: BaseTool
    permission: str = "read"
    source: str = "builtin"
    mcp_server: str | None = None
    timeout: float | None = None
    inject_conversation: bool = False
    format_result: Callable | None = None

    @classmethod
    def from_tool(cls, tool, **options):
        schema = tool.tool_call_schema
        schema = dict(schema) if isinstance(schema, dict) else schema.model_json_schema()
        schema.setdefault("additionalProperties", False)
        return cls(tool.name, tool.description, schema, tool, **options)


def validation_error(spec, arguments):
    """Pure validation, safe to repeat before a LangGraph interrupt."""
    errors = list(Draft202012Validator(spec.json_schema).iter_errors(arguments))
    if not errors:
        return None
    error = errors[0]
    path = ".".join(map(str, error.absolute_path)) or "参数"
    # Schema errors can include the entire invalid string. Do not feed an
    # oversized payload back into the model merely to explain a length error.
    detail = error.message if len(error.message) <= 250 else f"{error.validator} 限制未满足"
    return f"参数校验未通过：{path}: {detail}。请修正参数或向用户追问。"


class ToolRegistry:
    def __init__(
        self,
        repository,
        retrieval=None,
        top_k=5,
        *,
        settings=None,
        mcp_client=None,
        scan=True,
        package="app.tools.builtin",
    ):
        self.repository, self.retrieval, self.top_k = repository, retrieval, top_k
        self.settings = settings or Settings()
        self.specs, self.tools = {}, {}
        if mcp_client is None:
            from app.tools.mcp_client import MCPDiscovery

            mcp_client = MCPDiscovery(self.settings)
        self.mcp_client = mcp_client
        if scan:
            self.scan_builtins(package)

    def scan_builtins(self, package="app.tools.builtin"):
        module = importlib.import_module(package)
        for item in sorted(
            pkgutil.iter_modules(module.__path__, module.__name__ + "."),
            key=lambda value: value.name,
        ):
            plugin = importlib.import_module(item.name)
            register = getattr(plugin, "register", None)
            if register:
                register(self)

    def register(self, spec):
        if spec.name in self.specs:
            logger.warning("duplicate tool dropped: %s (%s)", spec.name, spec.source)
            return False
        Draft202012Validator.check_schema(spec.json_schema)
        spec.permission = "write" if spec.name in WRITE_TOOLS else "read"
        self.specs[spec.name] = spec
        self.tools[spec.name] = spec.tool
        return True

    async def get_all_specs(self):
        merged = dict(self.specs)
        if self.mcp_client:
            for spec in await self.mcp_client.fetch_mcp_specs():
                if spec.name in merged:
                    logger.warning("duplicate tool dropped: %s (%s)", spec.name, spec.source)
                    continue
                spec.permission = "write" if spec.name in WRITE_TOOLS else "read"
                merged[spec.name] = spec
        return list(merged.values())


async def get_all_tools(registry: ToolRegistry):
    return [spec.tool for spec in await registry.get_all_specs()]
