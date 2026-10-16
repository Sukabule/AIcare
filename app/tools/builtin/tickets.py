import asyncio
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from app.tools.registry import ToolSpec


def register(registry):
    repository = registry.repository

    @tool
    async def create_ticket(
        description: Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")],
        ticket_type: Literal["售后", "投诉", "咨询", "其他"],
        conversation_id: Annotated[int, InjectedToolArg],
    ) -> dict:
        """生成待用户确认的工单预览（本次调用先暂停，不直接提交）。

        用户明确要求建工单或人工跟进，且 description 问题描述和 ticket_type 类型齐备时立即调用。
        只有这两个必填参数；订单号、使用时长、漏水位置均不是必填，不要为这些信息阻止预览。
        缺描述或类型时先追问，不编造。前端卡片确认后才写库，取消不创建，不重复调用。
        """
        if registry.settings and registry.settings.demo_ticket_delay_seconds:
            await asyncio.sleep(registry.settings.demo_ticket_delay_seconds)
        ticket = await repository.create_ticket(conversation_id, description, ticket_type)
        return {
            "source": "tickets",
            "ticket_no": ticket.ticket_no,
            "conversation_id": ticket.conversation_id,
            "status": ticket.status,
            "conversation_status": "已转人工",
        }

    registry.register(ToolSpec.from_tool(create_ticket, inject_conversation=True))
