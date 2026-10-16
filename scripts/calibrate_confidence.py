"""Calibrate on real A/B/C and D retrieval; reuse validated actual retrieval cache."""

import argparse
import asyncio
from datetime import UTC, datetime

from ch09_common import REPORTS, cached_retrieval, samples
from sqlalchemy import select

from app.config import Settings
from app.core.confidence import calibrate_threshold, compute_evidence_confidence
from app.core.evaluation import write_report
from app.db.base import Database
from app.db.models import KnowledgeChunk
from app.kb.repository import record
from app.kb.service import KnowledgeService


async def run(fresh=False):
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    try:
        async with db.sessions() as session:
            knowledge = [record(row) for row in await session.scalars(select(KnowledgeChunk))]
        cached = {} if fresh else cached_retrieval(settings, knowledge)
        service, rows, cached_count = KnowledgeService(settings, db.sessions), [], 0
        for sample in samples():
            if sample["bucket"] == "E_multi":
                continue
            item = cached.get(sample["id"])
            if item and item["query"] == sample["query"]:
                hits = item["hits"]
                cached_count += 1
            else:
                hits = await service.retrieval.search_knowledge(
                    sample["query"], 10, strategy="hybrid_rerank", apply_understanding=False
                )
            value = compute_evidence_confidence(hits)
            rows.append(
                dict(
                    id=sample["id"],
                    bucket=sample["bucket"],
                    query=sample["query"],
                    score=value.score,
                    signals=value.signals,
                    answerable=not sample["should_refuse"],
                )
            )
        report = {
            **calibrate_threshold(rows),
            "calibrated_at": datetime.now(UTC).isoformat(),
            "knowledge_chunks": len(knowledge),
            "embedding_model": settings.embed_model,
            "rerank_model": settings.rerank_model,
            "source": "历史真实检索缓存（逐块核验当前原文）"
            if cached_count == len(rows)
            else "真实检索（含经核验的缓存）",
            "cached_count": cached_count,
            "fresh_count": len(rows) - cached_count,
            "weights_note": "固定四信号权重，仅校准阈值",
        }
        write_report(REPORTS / "confidence_calibration.json", report)
        text = (
            f"样本 {len(rows)}；真实缓存 {cached_count}；新检索 {len(rows) - cached_count}\n"
            f"推荐阈值 {report['recommended_threshold']}；Youden J {report['youden_j']:.4f}\n"
        )
        text += "\n".join(f"{r['id']}\t{r['bucket']}\t{r['score']:.6f}" for r in rows)
        (REPORTS / "confidence_calibration.txt").write_text(text, "utf-8")
        print(text[:250])
        return report
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true", help="强制新检索，消耗嵌入与重排调用")
    asyncio.run(run(parser.parse_args().fresh))
