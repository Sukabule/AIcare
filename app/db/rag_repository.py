from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert

from app.db.models import FaithCase, LowConfidenceQuestion


def record(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class RagRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def low_confidence(
        self, conversation_id, raw_question, source, reason, retrieved_chunks=None
    ):
        if source not in {"retrieval_low_conf", "self_check", "user_feedback"}:
            raise ValueError("未知问题池入口")
        async with self.sessions.begin() as session:
            row = LowConfidenceQuestion(
                conversation_id=conversation_id,
                raw_question=raw_question,
                source=source,
                reason=reason,
                retrieved_chunks=retrieved_chunks,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def upsert_case(self, case):
        values = {
            key: case[key]
            for key in (
                "eval_id",
                "bucket",
                "query",
                "strategy",
                "answer",
                "reason",
                "citations",
                "judge_model",
            )
        }
        statement = insert(FaithCase).values(**values, status="未解决", seen_count=1)
        async with self.sessions.begin() as session:
            await session.execute(
                statement.on_duplicate_key_update(
                    **values,
                    status="未解决",
                    resolution=None,
                    resolved_at=None,
                    seen_count=FaithCase.seen_count + 1,
                    last_seen_at=func.now(),
                )
            )

    async def cases(self, status=None, page=1, page_size=20):
        conditions = [FaithCase.status == status] if status else []
        async with self.sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(FaithCase).where(*conditions)
            )
            rows = await session.scalars(
                select(FaithCase)
                .where(*conditions)
                .order_by(FaithCase.last_seen_at.desc(), FaithCase.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return {"total": total, "items": [record(row) for row in rows], "page": page}

    async def resolve(self, case_id, status, resolution):
        if status not in {"已解决", "无需解决"} or not resolution.strip():
            raise ValueError("处置必须选择状态并填写说明")
        async with self.sessions.begin() as session:
            row = await session.get(FaithCase, case_id, with_for_update=True)
            if row is None:
                raise LookupError("个案不存在")
            row.status, row.resolution = status, resolution.strip()
            row.resolved_at = datetime.now(UTC).replace(tzinfo=None)

    async def manual_summary(self, current_ids):
        async with self.sessions() as session:
            total = await session.scalar(select(func.count()).select_from(FaithCase))
            confirmed = await session.scalar(
                select(func.count())
                .select_from(FaithCase)
                .where(FaithCase.eval_id.in_(current_ids), FaithCase.status == "已解决")
            )
            return {"cumulative_cases": total, "current_confirmed": confirmed}
