# ruff: noqa: E402
"""Replay explicit boundary examples and manually disposed faith cases at temperature zero."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select

from app.config import Settings
from app.core.evaluation import write_report
from app.core.llm import create_chat_model
from app.core.prompts import JUDGE_PROMPT
from app.db.base import Database
from app.db.models import FaithCase
from scripts.eval_ch04 import Judgment


async def run():
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    model = create_chat_model(settings.model_copy(update={"chat_model": settings.judge_model}))
    model.temperature = 0
    model.max_tokens = 2048
    model.extra_body = {"enable_thinking": False}
    cases = json.loads((ROOT / "tests/data/judge_calibration.json").read_text("utf-8"))
    async with db.sessions() as session:
        for row in await session.scalars(select(FaithCase).where(FaithCase.status != "未解决")):
            cases.append(
                dict(
                    id="ledger:" + row.eval_id,
                    query=row.query,
                    answer=row.answer,
                    citations=row.citations,
                    faithful=row.status == "无需解决",
                )
            )
    judge = model.with_structured_output(Judgment)
    results = []
    semaphore = asyncio.Semaphore(3)

    async def replay(case):
        async with semaphore:
            value = dict(id=case["id"], expected=case["faithful"])
            try:
                async with asyncio.timeout(45):
                    result = await judge.ainvoke(
                        JUDGE_PROMPT.format_messages(
                            query=case["query"],
                            points="0 个标准要点",
                            evidence=json.dumps(case["citations"], ensure_ascii=False),
                            answer=case["answer"],
                        )
                    )
                value.update(
                    actual=result.faithful,
                    reason=result.reason,
                    match=result.faithful == case["faithful"],
                )
            except Exception as exc:
                value.update(error=type(exc).__name__, match=False)
            results.append(value)
            print(f"{case['id']}: {'PASS' if value['match'] else 'FAIL'}", flush=True)

    try:
        await asyncio.gather(*(replay(case) for case in cases))
        report = dict(
            total=len(results),
            matched=sum(r["match"] for r in results),
            results=results,
            judge_model=settings.judge_model,
            temperature=0,
        )
        write_report(ROOT / "data/ch04/reports/judge_check.json", report)
        print(f"Judge agreement {report['matched']}/{report['total']}", flush=True)
        return 0 if report["matched"] == report["total"] else 1
    finally:
        await db.close()
        await model.root_async_client.close()
        model.root_client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
