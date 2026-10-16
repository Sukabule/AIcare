from sqlalchemy import func, select

from app.db.models import KnowledgeChunk, LowConfidenceQuestion, ReviewQueue


async def test_feedback_up_down_snapshots_and_stale_question(client, application, repo, db):
    runtime = application.state.agent_factory()
    cid = (await repo.get_or_create("feedback", None)).id
    graph = await runtime.ensure()
    await graph.aupdate_state(
        runtime.config(cid),
        {
            "question": "邮费是多少",
            "retrieved_snapshot": [
                {"question": "运费怎么算", "answer": "满99包邮", "rerank_score": 0.9}
            ],
        },
    )
    assert (
        await client.post(
            "/api/feedback", json={"conversation_id": cid, "rating": "up", "question": "邮费是多少"}
        )
    ).status_code == 200
    async with db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(LowConfidenceQuestion)) == 0
    for question in ["邮费是多少", "较早一轮问题"]:
        response = await client.post(
            "/api/feedback", json={"conversation_id": cid, "rating": "down", "question": question}
        )
        assert response.status_code == 200
    async with db.sessions() as s:
        rows = list(
            await s.scalars(select(LowConfidenceQuestion).order_by(LowConfidenceQuestion.id))
        )
        assert (
            rows[0].source == "user_feedback"
            and rows[0].retrieved_chunks[0]["answer"] == "满99包邮"
        )
        assert rows[1].retrieved_chunks is None
    assert (
        await client.post(
            "/api/feedback", json={"conversation_id": 999999, "rating": "down", "question": "问题"}
        )
    ).status_code == 404


async def test_review_approve_reuses_pending_vectorize_and_rolls_back_failures(
    client, application, repo, db
):
    item = await repo.insert_review_item("用户该如何办理咨询", "待审核草稿")

    async def embeddings(texts):
        return [[0.0] * 1024 for text in texts]

    calls, removed = [], []

    async def upsert(rows):
        calls.extend(rows)

    async def remove(ids):
        removed.extend(ids)

    application.state.kb.embeddings.embed_texts = embeddings
    application.state.kb.vectors.upsert = upsert
    application.state.kb.vectors.remove = remove
    response = await client.post(
        f"/api/review/{item['id']}/approve", json={"approved_answer": "联系客服，由人工提供说明。"}
    )
    assert response.status_code == 200 and len(calls) == 1
    async with db.sessions() as s:
        row = await s.get(ReviewQueue, item["id"])
        assert row.review_status == "通过" and row.approved_answer == "联系客服，由人工提供说明。"
        chunk = await s.scalar(select(KnowledgeChunk))
        assert chunk.vectorize_status == "done"
    assert (await client.post(f"/api/review/{item['id']}/reject", json={})).status_code == 409
    second = await repo.insert_review_item("另一个问题", "草稿")

    async def failed(rows):
        calls.extend(rows)
        raise ConnectionError("partial upsert")

    application.state.kb.vectors.upsert = failed
    response = await client.post(
        f"/api/review/{second['id']}/approve", json={"approved_answer": "核准内容"}
    )
    assert response.status_code == 502 and removed == [calls[-1]["id"]]
    async with db.sessions() as s:
        assert (await s.get(ReviewQueue, second["id"])).review_status == "待审"
        assert await s.scalar(select(func.count()).select_from(KnowledgeChunk)) == 1
    detail = (await client.get(f"/api/review/{second['id']}")).json()
    assert detail["review_status"] == "待审" and detail["raw_questions"] == []
    assert (await client.post(f"/api/review/{second['id']}/reject", json={})).status_code == 200
    assert (await client.get("/api/review/queue?status=驳回")).json()["items"][0]["id"] == second[
        "id"
    ]
