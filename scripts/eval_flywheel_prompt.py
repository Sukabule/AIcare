"""Small real-model labeled normalization/dedup evaluation; never writes review rows."""

import asyncio
import json
from datetime import UTC, datetime

from ch09_common import REPORTS, ROOT, close_model, configure_model

from app.config import Settings
from app.core.evaluation import write_report
from app.core.flywheel import Flywheel


async def run():
    settings = Settings()
    model = configure_model(settings)
    records = []
    path = ROOT / "tests/data/flywheel_samples.json"
    try:
        wheel = Flywheel(None, model)
        for sample in json.loads(path.read_text("utf-8")):
            value = await wheel.normalize(sample["question"], sample["candidates"])
            semantic = all(
                any(word.lower() in value.normalized_question.lower() for word in group)
                for group in sample["concepts"]
            )
            matched = value.matched_question_id == sample["matched_id"]
            records.append(
                dict(
                    id=sample["id"],
                    passed=semantic and matched,
                    normalize_ok=semantic,
                    dedup_ok=matched,
                    input=sample,
                    output=value.model_dump(),
                )
            )
            print(f"{sample['id']}: {'PASS' if records[-1]['passed'] else 'FAIL'}", flush=True)
        passed = sum(row["passed"] for row in records)
        report = dict(
            model=settings.chat_model,
            created_at=datetime.now(UTC).isoformat(),
            size=len(records),
            passed=passed,
            accuracy=passed / len(records),
            scoring="标准化概念覆盖 + 候选 ID；示例答案仍须人工核准",
            records=records,
        )
        write_report(REPORTS / "flywheel_prompt.json", report)
        print(f"标注样例：{passed}/{len(records)} ({report['accuracy']:.1%})")
        if report["accuracy"] < 0.8:
            raise SystemExit("标准化/查重标注验证未达到 80%")
    finally:
        await close_model(model)


if __name__ == "__main__":
    asyncio.run(run())
