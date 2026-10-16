"""Five real graph acceptance scenarios against the running FastAPI service."""

# ruff: noqa: E402
import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings
from app.core.evaluation import write_report
from app.db.base import Database
from app.db.models import Ticket

CASES = [
    ("policy", "邮费是多少", "knowledge"),
    ("logistics", "订单1001的物流到哪了", "business"),
    ("complaint", "我要投诉订单1001的服务，请查物流并帮我建工单", "escalate"),
    ("chitchat", "你好", "fallback_script"),
    ("dependent-tools", "请查订单1001的商品、金额，再查它的物流单号和配送轨迹", "business"),
]


async def run(base_url, output=None):
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    report_path = ROOT / (output or "data/ch05/reports/acceptance.json")
    results = []
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=240, trust_env=False) as client:
            for name, question, route in CASES:
                response = await client.post(
                    "/api/agent",
                    json={
                        "user_id": "ch05-eval-" + uuid4().hex,
                        "message": question,
                    },
                )
                row = {"case": name, "query": question}
                try:
                    response.raise_for_status()
                    result = response.json()
                    trace = result["trace"]
                    passed = trace["route"] == route
                    if route == "knowledge":
                        passed &= "retrieve_knowledge" in trace["nodes"] and bool(
                            result["citations"]
                        )
                    if route == "escalate":
                        passed &= (
                            "main_agent" not in trace["nodes"]
                            and len(result["suggested_actions"]) == 2
                        )
                        async with db.sessions() as session:
                            passed &= (
                                await session.scalar(
                                    select(func.count())
                                    .select_from(Ticket)
                                    .where(Ticket.conversation_id == result["conversation_id"])
                                )
                            ) == 0
                    if route == "fallback_script":
                        passed &= trace["steps"] == 0 and "main_agent" not in trace["nodes"]
                    if name in ("logistics", "dependent-tools"):
                        selected = [c["name"] for c in result["tool_calls"]]
                        passed &= (
                            selected[:2] == ["query_order", "query_logistics"]
                            and trace["steps"] > 1
                        )
                        order = next(
                            t["data"] for t in result["tool_results"] if t["name"] == "query_order"
                        )
                        logistics = next(
                            c for c in result["tool_calls"] if c["name"] == "query_logistics"
                        )
                        passed &= logistics["args"]["tracking_no"] == order["tracking_no"]
                    row.update(passed=bool(passed), result=result)
                except Exception as exc:
                    row.update(passed=False, error=type(exc).__name__)
                    if isinstance(exc, httpx.HTTPStatusError):
                        row["http_status"] = response.status_code
                        row["detail"] = response.json().get("detail")
                results.append(row)
                print(f"{name}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
                write_report(
                    report_path,
                    {
                        "verification_mode": "live_model",
                        "model": settings.chat_model,
                        "total": len(results),
                        "passed": sum(r["passed"] for r in results),
                        "results": results,
                    },
                )
        return 0 if all(r["passed"] for r in results) else 1
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", help="Separate report path relative to the project root")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.base_url, args.output)))
