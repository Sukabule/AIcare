import pytest

from app.core.confidence import calibrate_threshold, compute_evidence_confidence


def hit(score, key=False):
    return {"rerank_score": score, "is_key_clause": key}


def test_empty_single_equal_and_key_clause_signals():
    assert compute_evidence_confidence([]).score == 0
    single = compute_evidence_confidence([hit(0.9)])
    assert single.signals == {
        "top1_score": 0.9,
        "valid_count": 1,
        "margin": 0.9,
        "key_clause_hit": False,
    }
    equal = compute_evidence_confidence([hit(0.9), hit(0.9)])
    assert equal.signals["margin"] == 0 and equal.signals["valid_count"] == 2
    assert compute_evidence_confidence([hit(0.9, True)]).score > single.score
    assert 0 <= equal.score <= 1


def test_sorting_finite_scores_and_valid_count_cap():
    value = compute_evidence_confidence([hit(0.1), hit(0.8), hit(0.7, True)])
    assert value.signals["top1_score"] == 0.8
    assert value.signals["margin"] == pytest.approx(0.1)
    assert value.signals["valid_count"] == 2
    assert compute_evidence_confidence([hit(float("nan"))]).score == 0
    assert compute_evidence_confidence([hit(5)]).score <= 1
    assert compute_evidence_confidence([hit(0.9)] * 20).score <= 1


def test_calibration_uses_youden_scan_and_both_labels_required():
    rows = [
        {"score": s, "answerable": label}
        for s, label in [(0.8, True), (0.7, True), (0.2, False), (0.3, False)]
    ]
    report = calibrate_threshold(rows)
    assert 0.3 < report["recommended_threshold"] <= 0.7
    assert max(row["youden_j"] for row in report["scan"]) == 1
    assert report["counts"] == {"answerable": 2, "absent": 2}
    with pytest.raises(ValueError):
        calibrate_threshold(rows[:2])
