import pytest

from app.core.evaluation import read_report, retrieval_metrics, summarize


def test_atomic_report_retries_windows_read_sharing(tmp_path, monkeypatch):
    from pathlib import Path

    from app.core.evaluation import write_report

    original = Path.replace
    attempts = []

    def sharing_violation(path, target):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("temporary Windows reader sharing violation")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", sharing_violation)
    path = tmp_path / "report.json"
    write_report(path, {"complete": True})
    assert len(attempts) == 3
    assert path.read_text("utf-8") == '{\n  "complete": true\n}'


def test_cross_document_partial_credit_and_absent_exclusion():
    sample = {
        "should_refuse": False,
        "expect_sections_all": [["运费", "配送"], ["会员"]],
        "expect_points": ["满99元包邮", "3次"],
    }
    hits = [
        {"section_path": "配送", "answer": "满 99 元包邮"},
        {"section_path": "无关", "answer": ""},
        {"section_path": "会员", "answer": "每月3次"},
    ]
    scores = retrieval_metrics(sample, hits)
    assert scores["recall_at_5"] == 1
    assert scores["mrr"] == pytest.approx((1 + 1 / 3) / 2)
    assert scores["evidence_coverage"] == 1
    assert retrieval_metrics({"should_refuse": True}, hits) is None
    assert retrieval_metrics(sample, hits[:1])["recall_at_5"] == 0.5


def test_missing_or_corrupt_report_is_state(tmp_path):
    path = tmp_path / "report.json"
    assert read_report(path)["present"] is False
    path.write_text('{"meta":', encoding="utf-8")
    assert read_report(path)["present"] is False


def test_missing_generation_is_not_zero_and_has_denominator():
    records = [
        {
            "id": "A1",
            "bucket": "A_policy",
            "strategy": "vector",
            "retrieval": {"mrr": 1, "recall_at_5": 1, "evidence_coverage": 0.5},
            "generation": None,
        }
    ]
    result = summarize(records)
    assert result["retrieval"]["vector"]["A_policy"]["mrr"] == 1
    assert result["generation"] is None


def test_structurally_broken_report_is_unavailable(tmp_path):
    import json

    path = tmp_path / "report.json"
    for report in (
        {"meta": [], "retrieval": {}, "generation": None, "kpi": {}},
        {"meta": {}, "retrieval": {}, "generation": None, "kpi": {}},
        {"meta": {"dataset_size": 300}, "retrieval": [], "generation": None, "kpi": {}},
    ):
        path.write_text(json.dumps(report), "utf-8")
        assert read_report(path)["present"] is False
