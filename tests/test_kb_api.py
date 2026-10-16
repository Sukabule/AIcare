import pytest

from app.core.jobs import Jobs


async def test_preview_does_not_write_and_ingest_is_idempotent(client):
    payload = {"text": "# 新文档\n## 运费\n满99元包邮。", "content_type": "policy"}
    preview = await client.post("/api/kb/preview", json=payload)
    assert preview.status_code == 200
    assert len(preview.json()["chunks"]) == 1
    assert (await client.get("/api/kb/overview")).json()["mysql"]["total"] == 0
    saved = await client.post("/api/kb/ingest", json=payload)
    assert saved.json()["inserted"] == 1
    again = await client.post("/api/kb/ingest", json=payload)
    assert again.json()["skipped"] == 1


async def test_invalid_parameters(client):
    assert (
        await client.post("/api/kb/preview", json={"text": "x", "content_type": "shell"})
    ).status_code == 422
    assert (
        await client.post("/api/kb/search", json={"query": "x", "route": "unknown"})
    ).status_code == 422
    assert (await client.post("/api/jobs/not-a-job", json={})).status_code == 404
    assert (await client.post("/api/jobs/kb-reset", json={})).status_code == 422


async def test_vector_failure_keeps_pending(client, application):
    async def fail(*args):
        raise RuntimeError("upstream unavailable")

    application.state.kb.embeddings.embed_texts = fail
    response = await client.post(
        "/api/kb/ingest", json={"text": "# 新知识\n正文内容。", "vectorize": True}
    )
    assert response.status_code == 502
    assert response.json()["detail"]["inserted"] == 1
    assert (await application.state.kb.repository.overview())["pending"] == 1


async def test_admin_independent_failures(client, application):
    async def fail(*args):
        raise RuntimeError("offline")

    application.state.kb.repository.overview = fail
    application.state.kb.vectors.count = fail
    result = (await client.get("/api/admin/overview")).json()
    assert {m["name"] for m in result["modules"]} >= {
        "知识库",
        "审核队列",
        "RAG 评估",
        "观测与成本",
    }
    assert result["modules"][0]["status"] == "unavailable"
    assert not {"主题分布", "分类器验收"} & {m["name"] for m in result["modules"]}
    overview = (await client.get("/api/kb/overview")).json()
    assert overview["consistency"] == "unavailable"
    assert len(overview["materials"]) == 3


async def test_admin_reports_vector_outage_with_mysql_still_available(client, application):
    async def fail():
        raise RuntimeError("Milvus offline")

    application.state.kb.vectors.count = fail
    result = (await client.get("/api/admin/overview")).json()["modules"][0]
    assert result["status"] == "unavailable"
    assert "向量服务读不到" in result["summary"]


async def test_jobs_whitelist_and_reentry(tmp_path):
    jobs = Jobs(tmp_path)
    with pytest.raises(KeyError):
        await jobs.start("kb-preview; echo bad")

    class Process:
        returncode = None

    jobs.active["kb-preview"] = Process()
    with pytest.raises(BlockingIOError):
        await jobs.start("kb-preview")
