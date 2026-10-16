"""Real Ch04 labels and shared metrics, one hybrid strategy, explicit actual sample size."""

import argparse
import asyncio
from datetime import UTC, datetime
from statistics import mean

from ch09_common import REPORTS, close_model, configure_model, samples

from app.config import Settings
from app.core.evaluation import Judgment, evaluate_generation, retrieval_metrics, write_report
from app.core.query_understanding import QueryUnderstanding, SelfCheck
from app.db.base import Database
from app.db.repository import Repository
from app.kb.service import KnowledgeService


def trend_text(rows):
    lines = []
    for previous, current in zip(rows, rows[1:], strict=False):
        lines.append(
            f"轮次 {previous['id']} → {current['id']}"
            f"（{previous['dataset_size']} → {current['dataset_size']}题）"
        )
        for key in ("recall_at_10", "mrr", "faithfulness", "refusal_rate"):
            a, b = previous["metrics"].get(key), current["metrics"].get(key)
            if a is None or b is None:
                lines.append(f"{key}: 尚无有效分母")
                continue
            delta = b - a
            arrow = "↓ ⚠" if delta < 0 else "↑" if delta > 0 else "→"
            lines.append(f"{key}: {a:.4f} → {b:.4f} {arrow} {abs(delta):.4f}")
    return "\n".join(lines) or "第一轮已完成，等待下一轮对比。"


async def run(per_bucket=1, full=False, triggered_by="手动"):
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    model, judge_model = (
        configure_model(settings, tokens=1024),
        configure_model(settings, settings.judge_model),
    )
    dataset, records = samples(None if full else per_bucket), []
    report_path = REPORTS / ("eval_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".json")
    try:
        service = KnowledgeService(settings, db.sessions)
        service.retrieval.understanding = QueryUnderstanding(lambda: model)
        check, judge = SelfCheck(lambda: model), judge_model.with_structured_output(Judgment)
        for sample in dataset:
            hits = await service.retrieval.search_knowledge(
                sample["query"], 10, strategy="hybrid_rerank"
            )
            retrieval = retrieval_metrics(sample, hits, top_k=10)
            generation = await evaluate_generation(
                sample,
                hits,
                model,
                judge,
                check,
                judge_model=settings.judge_model,
                evidence_threshold=settings.evidence_confidence_threshold,
            )
            records.append(
                dict(
                    id=sample["id"],
                    bucket=sample["bucket"],
                    query=sample["query"],
                    retrieval=retrieval,
                    generation=generation,
                )
            )
            write_report(report_path, dict(status="running", planned=len(dataset), records=records))
            print(f"{sample['id']}: complete", flush=True)
        ranking = [row["retrieval"] for row in records if row["retrieval"] is not None]
        faithful = [
            row["generation"]["faithful"]
            for row in records
            if row["generation"]["faithful"] is not None
        ]
        refused = [
            row["generation"]["refused"]
            for row in records
            if row["generation"]["refused"] is not None
        ]
        metrics = dict(
            recall_at_10=mean(r["recall_at_10"] for r in ranking) if ranking else None,
            mrr=mean(r["mrr"] for r in ranking) if ranking else None,
            faithfulness=mean(faithful) if faithful else None,
            refusal_rate=mean(refused) if refused else None,
            faithfulness_n=len(faithful),
            refusal_n=len(refused),
            ranking_n=len(ranking),
            generation_model=settings.chat_model,
            judge_model=settings.judge_model,
            evidence_threshold=settings.evidence_confidence_threshold,
            full_dataset=full,
        )
        repository = Repository(db.sessions)
        row = await repository.insert_eval_run(triggered_by, len(dataset), metrics)
        write_report(report_path, dict(status="complete", run=row, records=records))
        history = list(reversed(await repository.list_eval_runs(10)))
        text = trend_text(history)
        (REPORTS / "eval_trend.txt").write_text(text, "utf-8")
        print(text)
    except Exception as exc:
        write_report(
            report_path,
            dict(
                status="failed",
                planned=len(dataset),
                completed=len(records),
                error=type(exc).__name__,
                records=records,
            ),
        )
        raise
    finally:
        await close_model(model)
        await close_model(judge_model)
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-per-bucket", type=int, default=1)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--triggered-by", choices=["手动", "定时"], default="手动")
    args = parser.parse_args()
    if not 1 <= args.sample_per_bucket <= 60:
        parser.error("每桶样本必须 1-60")
    asyncio.run(run(args.sample_per_bucket, args.full, args.triggered_by))
