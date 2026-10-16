"""Compatibility facade for earlier chapters; all calls use the Ch08 engine."""

from types import SimpleNamespace

from app.tools.engine import ToolEngine


class ToolExecutor(ToolEngine):
    def __init__(self, registry, timeout_seconds=5, read_retries=2, **options):
        super().__init__(
            registry,
            SimpleNamespace(
                tool_default_timeout=timeout_seconds,
                mcp_tool_timeout=timeout_seconds,
                tool_max_retries=read_retries,
            ),
            **options,
        )
