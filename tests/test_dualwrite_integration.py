from uuid import uuid4

import pytest

from app.config import Settings
from app.kb.dualwrite import vectorize_pending
from app.kb.milvus_client import Vectors
from app.kb.repository import KnowledgeRepository


async def test_real_mysql_milvus_resume_after_upsert(db):
    settings = Settings(milvus_collection="test_resume_" + uuid4().hex)
    vectors = Vectors(settings)
    repo = KnowledgeRepository(db.sessions)
    await repo.ingest(
        [dict(category="test", questions=f"问题{i}", answer=f"答案{i}") for i in range(3)]
    )

    class Embeddings:
        async def embed_texts(self, texts):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    mark_done = repo.mark_done
    attempts = 0

    async def crash(ids):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("interrupted between Milvus and MySQL")
        await mark_done(ids)

    repo.mark_done = crash
    try:
        with pytest.raises(RuntimeError):
            await vectorize_pending(repo, Embeddings(), vectors, 1)
        assert (await repo.overview())["pending"] == 2
        assert await vectors.count() == 2
        await vectorize_pending(repo, Embeddings(), vectors, 1)
        assert (await repo.overview())["pending"] == 0
        assert await vectors.count() == 3
        assert len(await vectors.search([1.0] + [0.0] * 1023, 5)) == 3
    finally:
        await vectors.reset()
