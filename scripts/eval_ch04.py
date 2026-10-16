# ruff: noqa: E402
"""Four retrieval strategies, exact user labels, resumable and partial-result-safe evaluation."""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select

from app.config import Settings
from app.core.evaluation import (
    REPORT_PATH,
    Judgment,
    UpstreamCircuit,
    evaluate_generation,
    retrieval_metrics,
    summarize,
    write_report,
)
from app.core.llm import create_chat_model
from app.core.query_understanding import QueryUnderstanding, SelfCheck
from app.core.retrieval import STRATEGIES
from app.db.base import Database
from app.db.models import KnowledgeChunk
from app.db.rag_repository import RagRepository
from app.kb.repository import record
from app.kb.service import KnowledgeService


class CachedEmbeddings:
    def __init__(self, values):
        self.values = values

    async def embed_texts(self, texts):
        return [self.values[text] for text in texts]


async def run(args):
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    service = KnowledgeService(settings, db.sessions)
    model = create_chat_model(settings)
    model.temperature = 0
    model.max_tokens = 2048
    model.extra_body = {"enable_thinking": False}
    judge_model = create_chat_model(
        settings.model_copy(update={"chat_model": settings.judge_model})
    )
    judge_model.temperature = 0
    judge_model.max_tokens = 2048
    judge_model.extra_body = {"enable_thinking": False}
    judge = judge_model.with_structured_output(Judgment)
    check = SelfCheck(lambda: model)
    rewrite = QueryUnderstanding(lambda: model)
    samples = [
        json.loads(line)
        for line in (ROOT / "tests/data/eval_ch04.jsonl").read_text("utf-8").splitlines()
    ]
    if args.sample_per_bucket:
        selected, counts = [], {}
        for sample in samples:
            bucket = sample["bucket"]
            if counts.get(bucket, 0) < args.sample_per_bucket:
                selected.append(sample)
                counts[bucket] = counts.get(bucket, 0) + 1
        samples = selected
    async with db.sessions() as session:
        knowledge = [
            record(row)
            for row in await session.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.id))
        ]
    signature = hashlib.sha256(
        json.dumps(
            [
                samples,
                knowledge,
                settings.embed_model,
                settings.rerank_model or "BAAI/bge-reranker-v2-m3",
                settings.chat_model,
                settings.judge_model,
                settings.rerank_min_score,
            ],
            sort_keys=True,
            default=str,
        ).encode()
        + b"".join(
            (ROOT / path).read_bytes()
            for path in ["app/core/retrieval.py", "app/core/prompts.py", "scripts/eval_ch04.py"]
        )
    ).hexdigest()
    cache_path = ROOT / ".cache" / ("ch04_eval_" + signature[:16] + ".json")
    resume_path = ROOT / args.resume_from if args.resume_from else cache_path
    cache = (
        json.loads(resume_path.read_text("utf-8"))
        if (args.resume or args.resume_from) and resume_path.exists()
        else {"embeddings": {}, "rewrites": {}, "records": {}}
    )
    if args.resume_from and not args.embeddings_only:
        if (
            cache.get("generation_model") != settings.chat_model
            or cache.get("judge_model") != settings.judge_model
        ):
            raise ValueError("断点模型与当前配置不一致；只能用 --embeddings-only 复用嵌入")
    if args.resume_from and not args.embeddings_only:
        expected_queries = {s["id"]: s["query"] for s in samples}
        current_knowledge = {row["id"]: row for row in knowledge}
        for row in cache["records"].values():
            if expected_queries.get(row["id"]) != row["query"]:
                raise ValueError("断点的问题与当前评估集不一致")
            for hit in row.get("hits", []):
                chunk = current_knowledge.get(hit["id"])
                if chunk is None or (chunk["questions"], chunk["answer"]) != (
                    hit["question"],
                    hit["answer"],
                ):
                    raise ValueError("断点知识与当前原文不一致，必须重跑检索")
    if args.embeddings_only:
        cache = {"embeddings": cache["embeddings"], "rewrites": {}, "records": {}}
    embedding_cache_path = (
        ROOT
        / ".cache"
        / (
            "eval_embeddings_"
            + hashlib.sha256(settings.embed_model.encode()).hexdigest()[:12]
            + ".json"
        )
    )
    if embedding_cache_path.exists():
        saved_embeddings = json.loads(embedding_cache_path.read_text("utf-8"))
        cache["embeddings"] = {**saved_embeddings, **cache["embeddings"]}
    cache["generation_model"] = settings.chat_model
    cache["judge_model"] = settings.judge_model
    report_path = ROOT / args.output if args.output else REPORT_PATH
    log_path = report_path.with_suffix(".txt")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8", buffering=1)
    started = time.monotonic()
    meta = {
        "started_at": datetime.now(UTC).isoformat(),
        "dataset_size": len(samples),
        "full_dataset_size": 300,
        "bucket_counts": {
            b: sum(s["bucket"] == b for s in samples) for b in {s["bucket"] for s in samples}
        },
        "knowledge_chunks": len(knowledge),
        "generation_model": settings.chat_model,
        "embedding_model": settings.embed_model,
        "rerank_model": settings.rerank_model or "BAAI/bge-reranker-v2-m3",
        "judge_model": settings.judge_model,
        "signature": signature,
        "resume": bool(args.resume or args.resume_from),
        "status": "running",
        "generation_requested": not args.retrieval_only,
        "temperature": 0,
        "enable_thinking": False,
        "annotation_notes": [
            "原始 300 题标签保持不变。D3 纸质发票、D30 海外配送等与材料存在可回答性冲突。",
            "会员折扣、积分兑换等部分标准要点与原文不一致；分数反映原始标注，不据标注改写知识。",
        ],
        "metric_notes": (
            "Recall@5/MRR 对跨章节组取平均，组内 | 为任选；D 不计排序。"
            "证据覆盖为规范化字面匹配。分数按已成功样本计算，显示完成分母。"
        ),
    }

    def emit(message):
        print(message, flush=True)
        log.write(message + "\n")

    def save():
        records = list(cache["records"].values())
        meta.update(
            updated_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=round(time.monotonic() - started),
            retrieval_completed=sum("hits" in r for r in records),
            generation_completed=sum(r.get("generation") is not None for r in records),
            expected_strategy_samples=len(samples) * 4,
            rewrite_fallbacks=rewrite.fallbacks,
        )
        write_report(embedding_cache_path, cache["embeddings"])
        write_report(cache_path, cache)
        write_report(report_path, {"meta": meta, **summarize(records), "samples": records})

    semaphore = asyncio.Semaphore(3)
    circuit = UpstreamCircuit()
    try:
        emit(
            f"Dataset {len(samples)} questions / five buckets; "
            f"knowledge {len(knowledge)} chunks; strategies {STRATEGIES}"
        )
        missing = [s["query"] for s in samples if s["query"] not in cache["embeddings"]]
        for offset in range(0, len(missing), 32):
            batch = missing[offset : offset + 32]
            values = await service.embeddings.embed_texts(batch)
            cache["embeddings"].update(zip(batch, values, strict=True))
            save()
        service.retrieval.embeddings = CachedEmbeddings(cache["embeddings"])

        async def retrieve(sample):
            async with semaphore:
                key = sample["id"]
                if all(
                    "hits" in cache["records"].get(key + ":" + strategy, {})
                    for strategy in STRATEGIES
                ):
                    return
                if key not in cache["rewrites"]:
                    try:
                        async with asyncio.timeout(20):
                            cache["rewrites"][key] = await rewrite(sample["query"])
                    except TimeoutError:
                        cache["rewrites"][key] = sample["query"]
                        rewrite.fallbacks += 1

                async def understood(query):
                    return cache["rewrites"][key]

                # Per-query wrapper avoids sharing mutable understanding callbacks across tasks.
                from app.core.retrieval import Retrieval

                retrieval = Retrieval(
                    service.retrieval.embeddings,
                    service.vectors,
                    settings.retrieval_min_score,
                    service.retrieval.reranker,
                    understanding=understood,
                )
                for strategy in STRATEGIES:
                    rid = key + ":" + strategy
                    existing = cache["records"].get(rid, {})
                    if "hits" in existing:
                        continue
                    row = {
                        "id": key,
                        "bucket": sample["bucket"],
                        "query": sample["query"],
                        "strategy": strategy,
                        "retrieval": None,
                        "generation": None,
                    }
                    if circuit.reason:
                        row["retrieval_error"] = "skipped:" + circuit.reason
                        cache["records"][rid] = row
                        continue
                    try:
                        async with asyncio.timeout(90):
                            hits = await retrieval.search_knowledge(
                                sample["query"], 10, strategy=strategy, apply_threshold=False
                            )
                        row.update(hits=hits, retrieval=retrieval_metrics(sample, hits))
                    except Exception as exc:
                        row["retrieval_error"] = type(exc).__name__
                        circuit.record(exc)
                    cache["records"][rid] = row
                emit(f"retrieval {key} ({len(cache['records'])}/{len(samples) * 4})")
                save()

        await asyncio.gather(*(retrieve(sample) for sample in samples))
        emit(
            "Retrieval stage complete. Generation stage begins."
            if not args.retrieval_only
            else "Retrieval-only run complete."
        )
        save()

        async def generate(sample, strategy):
            async with semaphore:
                row = cache["records"][sample["id"] + ":" + strategy]
                if "hits" not in row or row.get("generation") is not None:
                    return
                if circuit.reason:
                    row["generation_error"] = "skipped:" + circuit.reason
                    return
                try:
                    row["generation"] = await evaluate_generation(
                        sample,
                        row["hits"],
                        model,
                        judge,
                        check,
                        judge_model=settings.judge_model,
                        strategy=strategy,
                        rerank_min_score=settings.rerank_min_score,
                    )
                    row.pop("generation_error", None)
                except Exception as exc:
                    row["generation_error"] = type(exc).__name__
                    circuit.record(exc)
                emit(
                    f"generation {sample['id']} {strategy}: "
                    f"{'ok' if row.get('generation') else row.get('generation_error')}"
                )
                save()

        if not args.retrieval_only:
            await asyncio.gather(
                *(generate(sample, strategy) for sample in samples for strategy in STRATEGIES)
            )
        result = summarize(list(cache["records"].values()))
        if result["generation"]:
            repository = RagRepository(db.sessions)
            for case in result["generation"]["faithfulness_cases"]:
                await repository.upsert_case(case)
        meta["status"] = (
            "complete"
            if all(
                "hits" in r and (args.retrieval_only or r.get("generation"))
                for r in cache["records"].values()
            )
            else "partial"
        )
        meta["blocked_reason"] = circuit.reason
        save()
        emit(
            f"Finished {meta['status']}: retrieval {meta['retrieval_completed']}, "
            f"generation {meta['generation_completed']}; {report_path}"
        )
    finally:
        log.close()
        await judge_model.root_async_client.close()
        judge_model.root_client.close()
        await model.root_async_client.close()
        model.root_client.close()
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resume-from", help="显式恢复兼容代码修复前的断点；会校验问题与知识原文")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument(
        "--embeddings-only", action="store_true", help="仅复用断点嵌入，重跑改写、检索和生成"
    )
    parser.add_argument("--sample-per-bucket", type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
