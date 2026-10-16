from sqlalchemy import select

from app.db.models import KnowledgeChunk
from app.kb.documents import build_chunks
from app.kb.repository import KnowledgeRepository


async def test_ingest_idempotence_pointers_and_same_heading(db):
    repo = KnowledgeRepository(db.sessions)
    chunks = build_chunks("# 运费\n满99元包邮。", "policy")
    chunks.append({**chunks[0], "answer": "不足99元收8元运费。"})
    result = await repo.ingest(chunks)
    assert result["inserted"] == 2
    assert (await repo.ingest(chunks))["skipped"] == 2
    rows = await repo.pending(10)
    assert rows[0]["next_chunk_id"] == rows[1]["id"]
    assert rows[1]["prev_chunk_id"] == rows[0]["id"]
    await repo.mark_done([rows[0]["id"]])
    assert len(await repo.pending(10)) == 1
    assert (await repo.overview())["done"] == 1


async def test_staging_global_dedup_and_resume(db):
    repo = KnowledgeRepository(db.sessions)
    pairs = [dict(question="运费？", answer="8元"), dict(question="运费", answer="8元")]
    await repo.stage("batch1", "conv:1", pairs)
    await repo.stage("batch1", "conv:1", pairs)
    assert len(await repo.staging()) == 2
    assert await repo.promote() == 1
    assert await repo.promote() == 0
    rows = await repo.staging()
    assert {r["status"] for r in rows} == {"kept", "discarded"}
    async with db.sessions() as s:
        assert len(list(await s.scalars(select(KnowledgeChunk)))) == 1


async def test_identical_blocks_do_not_create_self_links(db):
    repo = KnowledgeRepository(db.sessions)
    chunk = dict(category="政策", questions="重复标题", answer="重复正文")
    result = await repo.ingest([chunk, chunk])
    assert result["inserted"] == 1
    assert result["skipped"] == 1
    row = (await repo.pending())[0]
    assert row["prev_chunk_id"] is None
    assert row["next_chunk_id"] is None
