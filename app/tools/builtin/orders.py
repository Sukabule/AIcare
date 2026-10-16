from langchain_core.tools import tool

from app.tools.business import ShortText, order_snapshot, seeded
from app.tools.registry import ToolSpec


@tool
async def query_order(order_id: ShortText) -> dict:
    """查询指定订单号的订单状态、金额、下单时间及商品名称，返回演示数据。

    查询物流时先用本工具取得 tracking_no，再调用 query_logistics，不能臆造物流单号。
    """
    return order_snapshot(order_id)


@tool
async def query_product(product_name: ShortText) -> dict:
    """查询商品名称对应的价格、库存、规格。返回可复现的演示数据。"""
    rng = seeded(product_name)
    return {
        "source": "mock",
        "product_name": product_name,
        "price": rng.randrange(3900, 25900) / 100,
        "stock": rng.randrange(0, 100),
        "specifications": {"款式": "标准款", "包装": "单件装"},
    }


def register(registry):
    for operation in (query_order, query_product):
        registry.register(ToolSpec.from_tool(operation))
