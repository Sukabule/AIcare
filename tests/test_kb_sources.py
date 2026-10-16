from app.kb.sources import materials


def test_configurable_directory_discovers_markdown_and_spec(tmp_path):
    (tmp_path / "product-specs.md").write_text("# 规格\n## MH-W40\n水箱4L。", "utf-8")
    (tmp_path / "returns-policy.md").write_text("# 退货\n正文。", "utf-8")
    (tmp_path / "ignored.txt").write_text("not markdown", "utf-8")
    result = materials(tmp_path)
    assert [m["name"] for m in result] == ["product-specs.md", "returns-policy.md"]
    assert result[0]["content_type"] == "spec"
    assert result[0]["chunks"][0]["questions"] == "MH-W40"
    assert result[1]["content_type"] == "policy"


def test_no_silent_fallback_for_missing_configured_directory(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        materials(tmp_path / "missing")
