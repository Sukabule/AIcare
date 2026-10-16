"""One governed execution pipeline, including denied/invalid/unknown calls."""

import asyncio
import json
import logging
from time import perf_counter

import httpx
from langchain_core.messages import ToolMessage

from app.schemas.agent import ToolResult as ToolRun
from app.tools.registry import WRITE_TOOLS, validation_error

logger = logging.getLogger(__name__)


def transient(exc):
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(transient(item) for item in exc.exceptions)
    return isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError))


class ToolEngine:
    def __init__(
        self, registry, settings, *, backoff=0.2, max_result_tokens=None, token_counter=None
    ):
        self.registry, self.settings, self.backoff = registry, settings, backoff
        self.max_result_tokens, self.token_counter = max_result_tokens, token_counter

    async def execute_tool_call(
        self,
        call,
        conversation_id,
        *,
        confirmed=False,
        raw_question="",
        denial_message=None,
        blocked_error=None,
    ) -> ToolRun:
        started = perf_counter()
        name, call_id, arguments = call["name"], call["id"], call.get("args", {})
        spec, status, retries = None, "失败", 0

        def failure(code, message):
            return ToolRun(
                name=name, tool_call_id=call_id, ok=False, error={"code": code, "message": message}
            )

        async def run():
            nonlocal spec, status, retries
            spec = next((s for s in await self.registry.get_all_specs() if s.name == name), None)
            if spec is None:
                return failure("unknown_tool", "未知工具，请如实告知无法查询。")
            error = validation_error(spec, arguments)
            if error:
                status = "校验拦下"
                return failure("invalid_arguments", error)
            if blocked_error:
                status = "校验拦下"
                return failure(blocked_error["code"], blocked_error["message"])
            # Permission belongs to local policy, never server annotations or model arguments.
            writing = name in WRITE_TOOLS
            if writing and confirmed is not True:
                status = "权限拒绝"
                return failure("permission_denied", denial_message or "建工单需要用户明确确认。")
            args = dict(arguments)
            if spec.inject_conversation:
                args["conversation_id"] = conversation_id
            schema = spec.tool.get_input_schema()
            properties = (
                schema.get("properties", {}) if isinstance(schema, dict) else schema.model_fields
            )
            if "raw_question" in properties and spec.source == "builtin":
                args["raw_question"] = raw_question
            timeout = spec.timeout or (
                self.settings.mcp_tool_timeout
                if spec.source == "mcp"
                else self.settings.tool_default_timeout
            )
            attempts = 1 if writing else 1 + self.settings.tool_max_retries
            for attempt in range(attempts):
                retries = attempt
                try:
                    data = await asyncio.wait_for(spec.tool.ainvoke(args), timeout)
                    if spec.format_result:
                        data = spec.format_result(data)
                    value = ToolRun(name=name, tool_call_id=call_id, ok=True, data=data)
                    if self.max_result_tokens is not None and self.token_counter is not None:
                        content = json.dumps(value.model_dump(), ensure_ascii=False)
                        if (
                            self.token_counter([ToolMessage(content=content, tool_call_id=call_id)])
                            > self.max_result_tokens
                        ):
                            return failure(
                                "tool_result_too_large",
                                "查询结果超过单条工具预算，请缩小范围或联系人工核实。",
                            )
                    status = "成功"
                    return value
                except Exception as exc:
                    if transient(exc) and attempt + 1 < attempts:
                        await asyncio.sleep(self.backoff * (attempt + 1))
                        continue
                    status = "超时" if isinstance(exc, TimeoutError) else "失败"
                    message = f"工具暂时不可用：{type(exc).__name__}，请如实告知，不得编造结果。"
                    if writing:
                        message += "工单提交结果可能不确定，请联系人工核实，不要自动重复提交。"
                    return failure("tool_timeout" if status == "超时" else "tool_failed", message)
            raise AssertionError("unreachable")

        cancelled = False
        try:
            result = await run()
        except asyncio.CancelledError:
            cancelled = True
            result = failure("tool_cancelled", "工具调用中断；写操作结果可能不确定，请勿重复提交。")
        except Exception as exc:
            result = failure("tool_failed", f"工具暂时不可用：{type(exc).__name__}。")
        try:
            await self.registry.repository.insert_tool_audit(
                conversation_id=conversation_id,
                tool_call_id=call_id,
                tool_name=name,
                tool_source=spec.source if spec else "unknown",
                mcp_server=spec.mcp_server if spec else None,
                arguments=arguments,
                result_summary=json.dumps(result.model_dump(), ensure_ascii=False)[:500],
                status=status,
                error_message=result.error["message"] if result.error else None,
                retry_count=retries,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except Exception:
            logger.exception("tool audit unavailable: %s / %s", name, call_id)
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def execute(self, call, conversation_id, raw_question="", *, confirmed=False):
        return await self.execute_tool_call(
            call, conversation_id, confirmed=confirmed, raw_question=raw_question
        )
