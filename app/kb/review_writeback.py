import logging

from sqlalchemy import select, update

from app.db.models import KnowledgeChunk, ReviewQueue
from app.db.review_repository import ReviewConflict
from app.kb.dualwrite import vectorize_pending, write_pending
from app.kb.repository import record

logger = logging.getLogger(__name__)


class WritebackFailed(RuntimeError):
    pass


class TransactionKnowledgeRepository:
    def __init__(self, repository, session):
        self.repository, self.session = repository, session
        self.ids, self.new_ids = [], []

    async def ingest(self, chunks):
        existing = set(await self.session.scalars(select(KnowledgeChunk.id)))
        result = await self.repository.ingest_into(self.session, chunks)
        self.ids = result["ids"]
        self.new_ids = [id_ for id_ in self.ids if id_ not in existing]
        return result

    async def pending(self, limit=16):
        return [
            record(row)
            for row in await self.session.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.id.in_(self.ids), KnowledgeChunk.vectorize_status == "pending"
                )
                .order_by(KnowledgeChunk.id)
                .limit(limit)
            )
        ]

    async def mark_done(self, ids):
        for id_ in ids:
            await self.session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == id_)
                .values(vector_id=str(id_), vectorize_status="done")
            )


class ReviewWriteback:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    async def approve(self, rid, answer):
        kb, scoped = self.knowledge, None
        async with kb.repository.lock(), kb.repository.lock("ingest"):
            try:
                async with kb.repository.sessions.begin() as session:
                    row = await session.get(ReviewQueue, rid, with_for_update=True)
                    if row is None:
                        raise LookupError("缺口不存在")
                    if row.review_status != "待审":
                        raise ReviewConflict("只允许处理待审缺口")
                    scoped = TransactionKnowledgeRepository(kb.repository, session)
                    result = await write_pending(
                        scoped,
                        [
                            dict(
                                category="人工审核 FAQ",
                                questions=row.normalized_question,
                                answer=answer,
                                content_type="faq",
                                section_path="人工审核 / " + row.normalized_question,
                                is_key_clause=False,
                            )
                        ],
                    )
                    count = await vectorize_pending(scoped, kb.embeddings, kb.vectors)
                    row.review_status, row.approved_answer = "通过", answer
                return {"status": "通过", "knowledge_ids": result["ids"], "vectorized": count}
            except (LookupError, ReviewConflict):
                raise
            except Exception as exc:
                if scoped and scoped.new_ids:
                    try:
                        await kb.vectors.remove(scoped.new_ids)
                    except Exception:
                        logger.exception("review vector cleanup unconfirmed: %s", scoped.new_ids)
                        raise WritebackFailed("审核已回滚，向量清理未确认，请核实后重试。") from exc
                raise WritebackFailed("审核与知识写入已回滚，请检查嵌入/向量服务后重试。") from exc
