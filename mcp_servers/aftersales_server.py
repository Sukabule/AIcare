import hashlib
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "aftersales",
    host="127.0.0.1",
    port=int(os.getenv("MCP_PORT", "8102")),
    stateless_http=True,
    json_response=True,
)


def seed(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest(), "big")


@mcp.tool()
async def query_warranty(order_id: str) -> dict:
    """查询订单商品是否在保，仅提供可复现的演示数据。"""
    return {
        "source": "mock",
        "order_id": order_id,
        "in_warranty": seed(order_id) % 3 != 0,
        "warranty_months": 12,
        "note": "实际保修以签收及核验结果为准",
    }


@mcp.tool()
async def query_return_status(order_id: str) -> dict:
    """查询订单退货处理进度，仅提供可复现的演示数据。"""
    status = ["等待寄回", "仓库验收中", "处理完成"][seed(order_id) % 3]
    return {"source": "mock", "order_id": order_id, "status": status}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
