"""Shared real-model labeled prompt evaluation; no offline acceptance server."""

# ruff: noqa: E402
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from langchain_core.messages import AIMessage, HumanMessage

from app.config import Settings
from app.core.coref import resolve_reference
from app.core.evaluation import UpstreamCircuit, write_report
from app.core.intent import IntentResult, history_text
from app.core.llm import create_chat_model
from app.core.prompts import INTENT_CLASSIFY_PROMPT
from app.core.query_understanding import expand_queries


async def run(kind, limit, smoke, resume, ch08=False):
    settings = Settings()
    name = settings.intent_model or settings.chat_model if kind == "intent" else settings.chat_model
    model = create_chat_model(settings.model_copy(update={"chat_model": name}))
    updates = {"temperature": 0, "max_tokens": 1024}
    if "siliconflow" in settings.chat_base_url:
        updates["extra_body"] = {"enable_thinking": False}
    model = model.model_copy(update=updates)
    sample_path = ROOT / (
        "tests/data/ch08_intent_samples.json" if ch08 else f"tests/data/{kind}_samples.json"
    )
    samples = json.loads(sample_path.read_text("utf-8"))
    if smoke and kind == "intent":
        seen, selected = set(), []
        for sample in samples:
            if sample["intent"] not in seen:
                selected.append(sample)
                seen.add(sample["intent"])
        samples = selected
    if limit:
        samples = samples[:limit]
    rows, circuit = [], UpstreamCircuit()
    path = ROOT / f"data/{'ch08' if ch08 else 'ch06'}/reports/{kind}_eval.json"
    cached = {}
    if resume and path.exists():
        previous = json.loads(path.read_text("utf-8"))
        if previous.get("model") == name:
            cached = {r["query"]: r for r in previous["results"]}
    try:
        for sample in samples:
            old = cached.get(sample["query"])
            if old and old.get("passed") and old.get("history", []) == sample.get("history", []):
                rows.append(old)
                continue
            history = [
                HumanMessage(content=m["text"])
                if m["role"] == "user"
                else AIMessage(content=m["text"])
                for m in sample.get("history", [])
            ]
            row = dict(sample)
            if old and not old.get("passed"):
                row["previous_attempt"] = old
            try:
                async with asyncio.timeout(90):
                    if kind == "intent":
                        value = await model.with_structured_output(
                            IntentResult, method="json_schema", strict=True
                        ).ainvoke(
                            INTENT_CLASSIFY_PROMPT.format_messages(
                                question=sample["query"], history=history_text(history)
                            )
                        )
                        result = IntentResult.model_validate(value)
                        row.update(
                            actual=result.model_dump(),
                            passed=result.intent == sample["intent"],
                            json_valid=True,
                        )
                    elif kind == "coref":
                        result = await resolve_reference(model, sample["query"], history)
                        passed = (
                            result == sample["expect_exact"]
                            if "expect_exact" in sample
                            else all(token in result for token in sample["expect_contains"])
                        )
                        row.update(actual=result, passed=passed)
                    else:
                        result = await expand_queries(model, sample["query"], sample["order"])
                        row.update(
                            actual={"queries": result},
                            passed=len(result) == 3
                            and all(
                                token in " ".join(result)
                                for token in sample.get("expect_contains", [])
                            ),
                        )
            except Exception as exc:
                circuit.record(exc)
                row.update(
                    passed=False,
                    error=type(exc).__name__,
                    http_status=getattr(exc, "status_code", None),
                )
            rows.append(row)
            report = {
                "verification_mode": "live_model",
                "model": name,
                "total": len(rows),
                "planned": len(samples),
                "passed": sum(r["passed"] for r in rows),
                "blocked_reason": circuit.reason,
                "results": rows,
            }
            write_report(path, report)
            print(f"{kind}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
            if circuit.reason:
                break
        write_report(
            path,
            {
                "verification_mode": "live_model",
                "model": name,
                "total": len(rows),
                "planned": len(samples),
                "passed": sum(r["passed"] for r in rows),
                "blocked_reason": circuit.reason,
                "results": rows,
            },
        )
        return 0 if len(rows) == len(samples) and all(r["passed"] for r in rows) else 1
    finally:
        await model.root_async_client.close()
        model.root_client.close()


def main(kind):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Run only N labeled examples")
    parser.add_argument("--smoke", action="store_true", help="Intent: one example per class")
    parser.add_argument(
        "--resume", action="store_true", help="Keep successful cases and retry failures only"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(kind, args.limit, args.smoke, args.resume)))
