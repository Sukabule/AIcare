import asyncio
import hashlib
import os
import random

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP(
    "logistics",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8101")),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "mcp-logistics:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    ),
)


@mcp.tool()
async def query_logistics(tracking_no: str) -> dict:
    """查询物流轨迹。tracking_no 必须先从 query_order 获取，不是订单号。演示数据。"""
    await asyncio.sleep(float(os.getenv("MOCK_DELAY_SECONDS", "0")))
    rng = random.Random(int.from_bytes(hashlib.sha256(tracking_no.encode()).digest(), "big"))
    location = rng.choice(["上海浦东配送站", "杭州西湖配送站", "广州天河配送站"])
    return {
        "source": "mock",
        "tracking_no": tracking_no,
        "status_code": "TRANSIT",
        "location": location,
        "timeline": [
            {"time": "2026-10-10 14:00:00", "event": "商家已发货"},
            {"time": "2026-10-11 07:30:00", "event": f"到达{location}"},
        ],
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
