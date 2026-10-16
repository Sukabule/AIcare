"""Small labeled summary-prompt check, never an automated conversation acceptance."""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.core.llm import create_chat_model
from app.core.summarizer import summarize_dialog


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1, choices=[1, 2, 3])
    args = parser.parse_args()
    settings = Settings()
    configured = settings.model_copy(
        update={
            "chat_model": settings.summary_model or settings.chat_model,
            "chat_base_url": settings.summary_base_url or settings.chat_base_url,
            "chat_api_key": settings.summary_api_key
            if settings.summary_api_key.get_secret_value()
            else settings.chat_api_key,
        }
    )
    updates = {"temperature": 0, "max_tokens": 512}
    if "siliconflow" in configured.chat_base_url:
        updates["extra_body"] = {"enable_thinking": False}
    model = create_chat_model(configured).model_copy(update=updates)
    sample_text = await asyncio.to_thread(
        Path("tests/data/summary_samples.json").read_text, "utf-8"
    )
    cases = json.loads(sample_text)[: args.limit]
    results = []
    try:
        for case in cases:
            try:
                summary = await summarize_dialog(model, case["old_summary"], case["dialog"])
                source = json.dumps(case["dialog"], ensure_ascii=False)
                checks = {
                    "schema_valid": True,
                    "facts": all(any(v in summary for v in group) for group in case["points"]),
                    "length": 50 <= len(summary) <= 200,
                    "no_forbidden": not any(v in summary for v in case["forbidden"]),
                    "no_new_numbers": set(re.findall(r"\d+", summary)).issubset(
                        set(re.findall(r"\d+", source))
                    ),
                }
                results.append(
                    {
                        "id": case["id"],
                        "summary": summary,
                        "checks": checks,
                        "passed": all(checks.values()),
                    }
                )
            except Exception as exc:
                # Avoid exposing upstream body, endpoint or credentials in reports.
                results.append({"id": case["id"], "passed": False, "error": type(exc).__name__})
    finally:
        await model.root_async_client.close()
        model.root_client.close()
    report = {
        "model": configured.chat_model,
        "tested": len(results),
        "total_samples": 3,
        "passed": sum(r["passed"] for r in results),
        "results": results,
        "browser_20_turn_acceptance": "reserved for user's manual browser chat",
    }
    output = Path("data/ch07/reports/summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text, json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["tested"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
