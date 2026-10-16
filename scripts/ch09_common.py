import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPORTS = ROOT / "data/ch09/reports"


def samples(per_bucket=None):
    rows = [
        json.loads(line)
        for line in (ROOT / "tests/data/eval_ch04.jsonl").read_text("utf-8").splitlines()
    ]
    if per_bucket is None:
        return rows
    selected, counts = [], {}
    for row in rows:
        bucket = row["bucket"]
        if counts.get(bucket, 0) < per_bucket:
            selected.append(row)
            counts[bucket] = counts.get(bucket, 0) + 1
    return selected


def cached_retrieval(settings, knowledge):
    """Only reuse real cached retrieval if all knowledge IDs and originals still match."""
    from app.core.evaluation import REPORT_PATH

    try:
        report = json.loads(REPORT_PATH.read_text("utf-8"))
        meta = report["meta"]
        if (
            meta["knowledge_chunks"] != len(knowledge)
            or meta["embedding_model"] != settings.embed_model
            or meta["rerank_model"] != (settings.rerank_model or "BAAI/bge-reranker-v2-m3")
        ):
            return {}
        source = {row["id"]: row for row in knowledge}
        cached = {}
        for row in report["samples"]:
            if row["strategy"] != "hybrid_rerank" or "hits" not in row:
                continue
            hits = []
            for hit in row["hits"]:
                original = source.get(hit["id"])
                if original is None or (original["questions"], original["answer"]) != (
                    hit["question"],
                    hit["answer"],
                ):
                    return {}
                hits.append({**hit, "is_key_clause": bool(original["is_key_clause"])})
            cached[row["id"]] = {"query": row["query"], "hits": hits}
        return cached
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def configure_model(settings, model_name=None, tokens=1024):
    from app.core.llm import create_chat_model

    model = create_chat_model(
        settings.model_copy(update={"chat_model": model_name or settings.chat_model})
    )
    updates = {"temperature": 0, "max_tokens": tokens}
    if "siliconflow" in settings.chat_base_url:
        updates["extra_body"] = {"enable_thinking": False}
    return model.model_copy(update=updates)


async def close_model(model):
    await model.root_async_client.close()
    model.root_client.close()
