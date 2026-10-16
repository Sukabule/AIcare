import asyncio
import json
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from statistics import mean

from pydantic import BaseModel, Field

from app.core.prompts import JUDGE_PROMPT, RAG_GENERATION_PROMPT
from app.core.rag import REFUSAL

REPORT_PATH = Path(__file__).resolve().parents[2] / "data/ch04/reports/rag_eval.json"


class UpstreamCircuit:
    def __init__(self):
        self.reason = None

    def record(self, error):
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        if status == 402:
            self.reason = "balance_insufficient"
        elif status in (400, 401, 403, 404) and self.reason is None:
            self.reason = "upstream_configuration_error"


def normalize(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", value).lower()
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def retrieval_metrics(sample, hits, top_k=5):
    if sample["should_refuse"]:
        return None
    groups = sample["expect_sections_all"]
    ranks = []
    for alternatives in groups:
        rank = next(
            (
                i
                for i, hit in enumerate(hits[:top_k], 1)
                if any(
                    normalize(section) in normalize(hit.get("section_path") or "")
                    for section in alternatives
                )
            ),
            0,
        )
        ranks.append(rank)
    evidence = normalize("\n".join(h["answer"] for h in hits))
    points = sample["expect_points"]
    return {
        f"recall_at_{top_k}": mean(bool(rank) for rank in ranks) if ranks else 0,
        "mrr": mean(1 / rank if rank else 0 for rank in ranks) if ranks else 0,
        "evidence_coverage": mean(normalize(p) in evidence for p in points) if points else None,
    }


def summarize(records):
    groups = defaultdict(list)
    for row in records:
        groups[(row["strategy"], row["bucket"])].append(row)
        groups[(row["strategy"], "overall")].append(row)
    retrieval, generation, cases = {}, {}, []
    for (strategy, bucket), rows in groups.items():
        measured = [r["retrieval"] for r in rows if r.get("retrieval") is not None]
        if measured:
            retrieval.setdefault(strategy, {})[bucket] = {
                **{
                    key: mean(r[key] for r in measured if r[key] is not None)
                    for key in ("mrr", "recall_at_5", "evidence_coverage")
                },
                "completed": len(measured),
                "expected": sum(r["bucket"] != "D_absent" for r in rows),
            }
        generated = [r for r in rows if r.get("generation") is not None]
        if generated:
            value = {"completed": len(generated), "expected": len(rows)}
            for key in ("answer_coverage", "faithful", "refused"):
                relevant = [
                    r["generation"][key]
                    for r in generated
                    if r["generation"].get(key) is not None
                    and ((key == "refused") == (r["bucket"] == "D_absent"))
                ]
                value[key] = mean(relevant) if relevant else None
                value[key + "_n"] = len(relevant)
            generation.setdefault(strategy, {})[bucket] = value
    for r in records:
        g = r.get("generation")
        if r["strategy"] == "hybrid_rerank" and g and g.get("faithful") is False:
            cases.append(
                {
                    "eval_id": r["id"],
                    "bucket": r["bucket"],
                    "query": r["query"],
                    "strategy": r["strategy"],
                    "answer": g["answer"],
                    "reason": g["reason"],
                    "citations": g["citations"],
                    "judge_model": g["judge_model"],
                }
            )
    if generation:
        generation["faithfulness_cases"] = cases
    overall = {s: b["overall"]["mrr"] for s, b in retrieval.items() if "overall" in b}
    best = max(overall, key=overall.get) if overall else None
    colloquial = retrieval.get("hybrid_rerank", {}).get("C_colloquial", {}).get("mrr")
    baseline = retrieval.get("vector", {}).get("C_colloquial", {}).get("mrr")
    live = generation.get("hybrid_rerank", {}).get("overall", {})
    faithful_n = live.get("faithful_n", 0)
    return {
        "retrieval": retrieval,
        "generation": generation or None,
        "kpi": {
            "best_strategy": best,
            "best_mrr": overall.get(best),
            "colloquial_mrr_gain": colloquial - baseline
            if colloquial is not None and baseline is not None
            else None,
            "answer_coverage": live.get("answer_coverage"),
            "refusal_rate": live.get("refused"),
            "faithfulness_n": faithful_n,
            "judge_cases": len(cases),
            "judge_hallucination_rate": len(cases) / faithful_n if faithful_n else None,
        },
    }


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), "utf-8")
    # Windows readers/virus scanners can briefly hold a sharing lock on the target.
    # Keep the previous complete JSON until atomic replacement succeeds.
    for attempt in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def read_report(path=REPORT_PATH):
    try:
        value = json.loads(Path(path).read_text("utf-8"))
        if not isinstance(value, dict) or not all(
            k in value for k in ("meta", "retrieval", "generation", "kpi")
        ):
            raise ValueError("invalid report")
        meta = value["meta"]
        required_meta = (
            "dataset_size",
            "knowledge_chunks",
            "embedding_model",
            "rerank_model",
            "judge_model",
            "status",
        )
        if (
            not isinstance(meta, dict)
            or not all(k in meta for k in required_meta)
            or not isinstance(value["retrieval"], dict)
            or not isinstance(value["kpi"], dict)
            or (value["generation"] is not None and not isinstance(value["generation"], dict))
        ):
            raise ValueError("invalid report structure")
        return {"present": True, **value}
    except (OSError, ValueError, TypeError):
        return {"present": False, "job": "eval-rag", "message": "尚无有效报告，请运行 RAG 评估。"}


def cited_numbers(answer):
    return [int(n) for n in re.findall(r"\[(\d+)\]", answer)]


class Judgment(BaseModel):
    covered_points: int = Field(ge=0)
    faithful: bool
    refused: bool
    reason: str


async def evaluate_generation(
    sample,
    hits,
    model,
    judge,
    check,
    *,
    judge_model,
    strategy="hybrid_rerank",
    rerank_min_score=0.5,
    evidence_threshold=None,
):
    from app.core.retrieval import arrange_evidence

    sufficient = bool(hits)
    reason = "没有召回证据"
    if strategy == "hybrid_rerank" and hits and max(h["score"] for h in hits) < rerank_min_score:
        sufficient = False
        reason = "低于重排阈值"
    if strategy == "hybrid_rerank" and evidence_threshold is not None:
        from app.core.confidence import compute_evidence_confidence

        confidence = compute_evidence_confidence(hits)
        sufficient = bool(hits) and confidence.score >= evidence_threshold
        reason = "校准置信度闸：" + str(confidence.signals)
    if sufficient:
        async with asyncio.timeout(45):
            sufficient, reason = await check(sample["query"], hits)
    citations = arrange_evidence(hits) if sufficient else []
    answer = REFUSAL
    if sufficient:
        async with asyncio.timeout(60):
            response = await model.ainvoke(
                RAG_GENERATION_PROMPT.format_messages(
                    query=sample["query"],
                    evidence=json.dumps(citations, ensure_ascii=False),
                )
            )
        answer = response.text
        if not answer:
            raise ValueError("empty answer")
    async with asyncio.timeout(45):
        judgment = await judge.ainvoke(
            JUDGE_PROMPT.format_messages(
                query=sample["query"],
                points="共 "
                + str(len(sample["expect_points"]))
                + " 个要点，只可逐项计一次：\n"
                + "\n".join(f"{i}. {p}" for i, p in enumerate(sample["expect_points"], 1)),
                evidence=json.dumps(citations, ensure_ascii=False),
                answer=answer,
            )
        )
    if judgment.covered_points > len(sample["expect_points"]):
        raise ValueError("judge covered_points exceeds expected points")
    return {
        "answer": answer,
        "citations": citations,
        "gate_reason": reason,
        "reason": judgment.reason,
        "judge_model": judge_model,
        "answer_coverage": judgment.covered_points / len(sample["expect_points"])
        if sample["expect_points"]
        else None,
        "faithful": judgment.faithful
        if strategy == "hybrid_rerank" and not sample["should_refuse"]
        else None,
        "refused": judgment.refused if sample["should_refuse"] else None,
    }
