from langchain_core.tools import tool

from app.tools.registry import ToolSpec


@tool
async def query_promotions() -> dict:
    """查询店铺当前优惠活动；返回演示活动，不代表真实促销。"""
    return {
        "source": "mock",
        "activities": [{"name": "新客礼包", "note": "演示优惠，请以结算页为准"}],
    }


def register(registry):
    registry.register(ToolSpec.from_tool(query_promotions))
