import math
from dataclasses import dataclass

WEIGHTS = {"top1_score": 0.75, "valid_count": 0.10, "margin": 0.10, "key_clause_hit": 0.05}
VALID_LINE = 0.5
VALID_CAP = 3


def relevance(hit):
    value = float(hit.get("rerank_score", hit.get("score", 0)) or 0)
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


@dataclass
class EvidenceConfidence:
    score: float
    signals: dict


def compute_evidence_confidence(hits):
    scores = sorted((relevance(hit) for hit in hits), reverse=True)
    top = scores[0] if scores else 0.0
    margin = top - scores[1] if len(scores) > 1 else top
    count = sum(score >= VALID_LINE for score in scores)
    key = any(hit.get("is_key_clause") is True for hit in hits)
    signals = dict(top1_score=top, valid_count=count, margin=margin, key_clause_hit=key)
    score = (
        WEIGHTS["top1_score"] * top
        + WEIGHTS["valid_count"] * min(count / VALID_CAP, 1)
        + WEIGHTS["margin"] * margin
        + WEIGHTS["key_clause_hit"] * key
    )
    return EvidenceConfidence(round(score, 6), signals)


def retrieved_snapshot(hits):
    return [
        {
            "question": h.get("question", ""),
            "answer": h.get("answer", ""),
            "rerank_score": relevance(h),
            "section_path": h.get("section_path", ""),
            "is_key_clause": h.get("is_key_clause") is True,
        }
        for h in hits[:3]
    ]


def calibrate_threshold(rows):
    positives = [r["score"] for r in rows if r["answerable"]]
    negatives = [r["score"] for r in rows if not r["answerable"]]
    if not positives or not negatives:
        raise ValueError("校准需要可答与应拒答两类真实样本")
    scan = []
    candidates = sorted(
        {0.0, 1.0, *[round(i / 1000, 3) for i in range(1001)], *[r["score"] for r in rows]}
    )
    for threshold in candidates:
        tpr = sum(score >= threshold for score in positives) / len(positives)
        fpr = sum(score >= threshold for score in negatives) / len(negatives)
        scan.append(
            dict(
                threshold=threshold,
                true_positive_rate=tpr,
                false_positive_rate=fpr,
                youden_j=tpr - fpr,
            )
        )
    # Max J, then retain most answerable samples, then choose the stricter boundary.
    best = max(scan, key=lambda row: (row["youden_j"], row["true_positive_rate"], row["threshold"]))
    return dict(
        recommended_threshold=best["threshold"],
        scan=scan,
        distribution=rows,
        counts=dict(answerable=len(positives), absent=len(negatives)),
        youden_j=best["youden_j"],
    )
