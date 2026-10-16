"""Four Ch06 acceptance checks using the running app and its real upstream model."""

# ruff: noqa: E402
import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings
from app.core.evaluation import write_report
from app.db.base import Database
from app.db.models import Conversation


async def collect(client, path, body):
    response = await client.post(path, json=body)
    response.raise_for_status()
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]
    if any(e.get("code") for e in events):
        raise RuntimeError(next(e["message"] for e in events if e.get("code")))
    if "data: [DONE]" not in response.text:
        raise RuntimeError("Incomplete SSE response")
    return events


async def run(base_url, resume=False):
    rows, user = [], "ch06-eval-" + uuid4().hex
    previous = {}
    path = ROOT / "data/ch06/reports/acceptance.json"
    if resume and path.exists():
        report = json.loads(path.read_text("utf-8"))
        if report.get("model") == Settings().chat_model:
            previous = {r["case"]: r for r in report["results"]}
            write_report(path.with_name("acceptance_previous.json"), report)
    async with httpx.AsyncClient(base_url=base_url, timeout=240, trust_env=False) as client:

        async def turn(query, cid=None):
            response = await client.post(
                "/api/agent", json={"user_id": user, "message": query, "conversation_id": cid}
            )
            response.raise_for_status()
            return response.json()

        try:
            cached = previous.get("intent-drift", {})
            if cached.get("passed"):
                first = cached["results"][0]
                db = Database(Settings().database_url.get_secret_value())
                try:
                    async with db.sessions() as session:
                        user = (await session.get(Conversation, first["conversation_id"])).user_id
                finally:
                    await db.close()
            else:
                first = await turn("订单1001的物流到哪了")
            cid = first["conversation_id"]
            second = await turn("这个能退吗", cid)
            third = await turn("还是想查它的物流", cid)
            results = [first, second, third]
            passed = [r["trace"]["intent"] for r in results] == ["物流", "退款退货", "物流"]
            passed &= all("1001" in r["trace"]["resolved_query"] for r in results)
            rows.append({"case": "intent-drift", "passed": passed, "results": results})
            rows.append(
                {
                    "case": "reference-refund-workflow",
                    "passed": second["trace"]["route"] == "refund_flow"
                    and "fetch_order" in second["trace"]["nodes"]
                    and "retrieve_policy" in second["trace"]["nodes"]
                    and len(second["trace"].get("expanded_queries", [])) == 3
                    and second["trace"].get("order_id") == "1001"
                    and "main_agent" in second["trace"]["nodes"]
                    and bool(second["citations"]),
                    "result": second,
                }
            )
            if previous.get("other-valid-json", {}).get("passed"):
                rows.append(previous["other-valid-json"])
            else:
                other = await turn("asdfgh")
                rows.append(
                    {
                        "case": "other-valid-json",
                        "passed": other["trace"]["intent"] == "其他"
                        and isinstance(other["trace"]["intent_confidence"], (float, int))
                        and other["trace"]["steps"] == 0,
                        "result": other,
                    }
                )
            if previous.get("interrupt-resume-sse", {}).get("passed"):
                rows.append(previous["interrupt-resume-sse"])
                return finish(rows)
            events = await collect(
                client, "/api/chat", {"user_id": user, "message": "我要申请退款"}
            )
            stop = next(e for e in events if e.get("event") == "interrupt")
            order = stop["orders"][0]["order_id"]
            resumed = await collect(
                client,
                "/api/actions/resume",
                {
                    "conversation_id": stop["conversation_id"],
                    "order_id": order,
                    "interrupt_id": stop["interrupt_id"],
                },
            )
            rows.append(
                {
                    "case": "interrupt-resume-sse",
                    "passed": not any(e.get("event") == "done" for e in events)
                    and resumed[-1].get("event") == "done"
                    and any(e.get("name") == "query_order" for e in resumed)
                    and any(e.get("delta") for e in resumed),
                    "interrupt": stop,
                    "events": resumed,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "case": "blocked",
                    "passed": False,
                    "error": type(exc).__name__,
                    "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                }
            )
    return finish(rows)


def finish(rows):
    report = {
        "verification_mode": "live_model",
        "model": Settings().chat_model,
        "total": 4,
        "completed": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "results": rows,
    }
    write_report(ROOT / "data/ch06/reports/acceptance.json", report)
    for row in rows:
        print(f"{row['case']}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
    return 0 if len(rows) == 4 and all(r["passed"] for r in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--resume", action="store_true", help="Reuse passed cases; recheck failed refund and drift"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.base_url, args.resume)))


if __name__ == "__main__":
    main()
