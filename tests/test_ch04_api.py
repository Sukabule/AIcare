async def test_report_missing_and_corrupt_are_states(client, application, tmp_path):
    application.state.rag_report_path = tmp_path / "missing.json"
    result = (await client.get("/api/rag-eval/report")).json()
    assert result["present"] is False
    application.state.rag_report_path.write_text('{"meta":', encoding="utf-8")
    assert (await client.get("/api/rag-eval/report")).json()["present"] is False


async def test_case_api_validates_disposition_and_pagination(client):
    assert (await client.get("/api/rag-eval/faith-cases?page=0")).status_code == 422
    assert (
        await client.post(
            "/api/rag-eval/faith-cases/1", json={"status": "已解决", "resolution": " "}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/rag-eval/faith-cases/987654", json={"status": "已解决", "resolution": "核验"}
        )
    ).status_code == 404


async def test_eval_job_heavy_confirmation_and_filter_keys(client):
    assert (await client.post("/api/jobs/eval-rag", json={})).status_code == 422
    assert (
        await client.post("/api/kb/search", json={"query": "x", "filters": {"injected": "x"}})
    ).status_code == 422
