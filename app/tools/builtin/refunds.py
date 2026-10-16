from langchain_core.tools import tool

from app.tools.business import ShortText
from app.tools.registry import ToolSpec


@tool
async def submit_refund(order_id: ShortText, reason: str = "") -> dict:
    """提议为已确认可申请退款的订单展示退款申请表。不会退款或创建工单，须用户提交确认。"""
    return {"proposed": True, "order_id": order_id, "reason": reason}


def register(registry):
    registry.register(ToolSpec.from_tool(submit_refund))
