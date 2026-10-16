from contextlib import asynccontextmanager

from sqlalchemy import delete, func, select, text, update

from app.db.models import Conversation, KnowledgeChunk, Message, QaExtractionStaging
from app.kb.dedup import fingerprint, normalize


def record(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


class KnowledgeRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @asynccontextmanager
    async def lock(self, name="pipeline"):
        # Database-scoped MySQL advisory lock coordinates CLI jobs and API workers.
        async with self.sessions() as session:
            lock_name = f"{await session.scalar(text('SELECT DATABASE()'))}:kb:{name}"
            acquired = await session.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name})
            if acquired != 1:
                raise BlockingIOError("知识库正在写入，请稍后重试")
            try:
                yield
            finally:
                await session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})

    async def ingest(self, chunks):
        async with self.lock("ingest"), self.sessions.begin() as s:
            return await self.ingest_into(s, chunks)

    async def ingest_into(self, s, chunks):
        existing = list(await s.scalars(select(KnowledgeChunk)))
        known = {fingerprint(r.questions, r.answer): r for r in existing}
        inserted, skipped, document = 0, 0, []
        for chunk in chunks:
            key = fingerprint(chunk["questions"], chunk["answer"])
            if key in known:
                skipped += 1
                if all(r.id != known[key].id for r, _ in document):
                    document.append((known[key], False))
                continue
            fields = {
                k: v
                for k, v in chunk.items()
                if k
                in {
                    "category",
                    "questions",
                    "answer",
                    "section_path",
                    "content_type",
                    "is_key_clause",
                }
            }
            row = KnowledgeChunk(**fields)
            s.add(row)
            await s.flush()
            known[key] = row
            document.append((row, True))
            inserted += 1
        for index, (row, fresh) in enumerate(document):
            if fresh:
                row.prev_chunk_id = document[index - 1][0].id if index else None
                row.next_chunk_id = document[index + 1][0].id if index + 1 < len(document) else None
        return dict(inserted=inserted, skipped=skipped, ids=[r.id for r, _ in document])

    async def pending(self, limit=16):
        async with self.sessions() as s:
            rows = await s.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.vectorize_status == "pending")
                .order_by(KnowledgeChunk.id)
                .limit(limit)
            )
            return [record(r) for r in rows]

    async def mark_done(self, ids):
        async with self.sessions.begin() as s:
            for id_ in ids:
                await s.execute(
                    update(KnowledgeChunk)
                    .where(KnowledgeChunk.id == id_)
                    .values(vector_id=str(id_), vectorize_status="done")
                )

    async def overview(self):
        async with self.sessions() as s:
            counts = dict(
                (
                    await s.execute(
                        select(KnowledgeChunk.vectorize_status, func.count()).group_by(
                            KnowledgeChunk.vectorize_status
                        )
                    )
                ).all()
            )
            stages = dict(
                (
                    await s.execute(
                        select(QaExtractionStaging.status, func.count()).group_by(
                            QaExtractionStaging.status
                        )
                    )
                ).all()
            )
            recent = await s.scalars(
                select(KnowledgeChunk).order_by(KnowledgeChunk.id.desc()).limit(30)
            )
            return dict(
                total=sum(counts.values()),
                pending=counts.get("pending", 0),
                done=counts.get("done", 0),
                key_clauses=await s.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunk)
                    .where(KnowledgeChunk.is_key_clause.is_(True))
                ),
                staging={k: stages.get(k, 0) for k in ("extracted", "kept", "discarded")},
                batches=await s.scalar(
                    select(func.count(func.distinct(QaExtractionStaging.batch_no)))
                ),
                recent=[record(r) for r in recent],
            )

    async def stage(self, batch_no, source_ref, pairs):
        async with self.sessions.begin() as s:
            if await s.scalar(
                select(QaExtractionStaging.id)
                .where(QaExtractionStaging.batch_no == batch_no)
                .limit(1)
            ):
                return
            s.add_all(
                [QaExtractionStaging(batch_no=batch_no, source_ref=source_ref, **p) for p in pairs]
            )

    async def staging(self, limit=200):
        async with self.sessions() as s:
            return [
                record(r)
                for r in await s.scalars(
                    select(QaExtractionStaging).order_by(QaExtractionStaging.id.desc()).limit(limit)
                )
            ]

    async def batch_exists(self, batch_no):
        async with self.sessions() as s:
            return bool(
                await s.scalar(
                    select(QaExtractionStaging.id)
                    .where(QaExtractionStaging.batch_no == batch_no)
                    .limit(1)
                )
            )

    async def promote(self):
        async with self.lock("ingest"), self.sessions.begin() as s:
            known = {normalize(q) for q in await s.scalars(select(KnowledgeChunk.questions))}
            rows = await s.scalars(
                select(QaExtractionStaging)
                .where(QaExtractionStaging.status == "extracted")
                .order_by(QaExtractionStaging.id)
                .with_for_update()
            )
            count = 0
            for row in rows:
                question = normalize(row.question)
                if not question or question in known:
                    row.status = "discarded"
                    continue
                s.add(
                    KnowledgeChunk(
                        category="客服问答",
                        questions=row.question,
                        answer=row.answer,
                        section_path=row.source_ref,
                        content_type="mined",
                    )
                )
                row.status = "kept"
                known.add(question)
                count += 1
            return count

    async def conversations(self):
        async with self.sessions() as s:
            # Only completed conversational answers, never tool protocol or personal account data.
            ids = list(await s.scalars(select(Conversation.id).order_by(Conversation.id)))
            result = []
            for id_ in ids:
                rows = await s.scalars(
                    select(Message).where(Message.conversation_id == id_).order_by(Message.id)
                )
                messages = [
                    dict(role=r.role, content=r.content)
                    for r in rows
                    if r.role in ("user", "assistant") and r.content and not r.tool_calls
                ]
                if messages:
                    result.append(dict(id=id_, messages=messages))
            return result

    async def reset(self):
        async with self.sessions.begin() as s:
            await s.execute(update(KnowledgeChunk).values(prev_chunk_id=None, next_chunk_id=None))
            await s.execute(delete(KnowledgeChunk))
            await s.execute(delete(QaExtractionStaging))
