import logging

from sqlalchemy import func, select

from app.db.models import EvalRun, LowConfidenceQuestion, ReviewQueue
from app.db.rag_repository import record
from app.kb.repository import KnowledgeRepository

logger = logging.getLogger(__name__)


class ReviewConflict(ValueError):
    pass


class ReviewRepository:
    """Mixin for Repository; short transactions with a guarded atomic merge."""

    def flywheel_lock(self):
        return KnowledgeRepository(self.sessions).lock("flywheel")

    async def fetch_unmatched_low_conf(self, limit=100):
        async with self.sessions() as session:
            return [
                record(row)
                for row in await session.scalars(
                    select(LowConfidenceQuestion)
                    .where(LowConfidenceQuestion.matched_review_id.is_(None))
                    .order_by(LowConfidenceQuestion.id)
                    .limit(limit)
                )
            ]

    async def list_review_candidates(self, limit=200):
        async with self.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(ReviewQueue))
            if count > limit:
                logger.warning(
                    "flywheel candidates truncated: %s -> %s (all statuses)", count, limit
                )
            return [
                {"id": row.id, "normalized_question": row.normalized_question}
                for row in await session.scalars(
                    select(ReviewQueue)
                    .order_by(ReviewQueue.updated_at.desc(), ReviewQueue.id.desc())
                    .limit(limit)
                )
            ]

    async def insert_review_item(self, normalized_question, ai_suggested_answer):
        async with self.sessions.begin() as session:
            row = ReviewQueue(
                normalized_question=normalized_question, ai_suggested_answer=ai_suggested_answer
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return record(row)

    async def apply_normalization(self, lcq_id, value, candidate_ids):
        async with self.sessions.begin() as session:
            raw = await session.get(LowConfidenceQuestion, lcq_id, with_for_update=True)
            if raw is None or raw.matched_review_id is not None:
                return {"processed": 0, "created": 0, "merged": 0}
            matched = value.matched_question_id
            if matched is not None:
                if matched not in candidate_ids:
                    raise ValueError("模型返回了不在候选中的缺口 ID")
                item = await session.get(ReviewQueue, matched, with_for_update=True)
                if item is None:
                    raise ValueError("候选缺口不存在")
                item.occurrence_count += 1
            else:
                item = ReviewQueue(
                    normalized_question=value.normalized_question,
                    ai_suggested_answer=value.ai_suggested_answer,
                )
                session.add(item)
                await session.flush()
            raw.matched_review_id = item.id
            return {
                "processed": 1,
                "created": int(matched is None),
                "merged": int(matched is not None),
            }

    async def increment_occurrence(self, rid):
        async with self.sessions.begin() as session:
            item = await session.get(ReviewQueue, rid, with_for_update=True)
            if item is None:
                raise LookupError("缺口不存在")
            item.occurrence_count += 1

    async def set_matched_review(self, lcq_id, review_id):
        async with self.sessions.begin() as session:
            item = await session.get(LowConfidenceQuestion, lcq_id, with_for_update=True)
            if item is None:
                raise LookupError("原话不存在")
            if item.matched_review_id is not None:
                raise ReviewConflict("原话已归并")
            item.matched_review_id = review_id

    async def list_review_queue(self, status=None):
        async with self.sessions() as session:
            query = select(ReviewQueue)
            if status:
                query = query.where(ReviewQueue.review_status == status)
            return [
                record(row)
                for row in await session.scalars(
                    query.order_by(
                        ReviewQueue.occurrence_count.desc(), ReviewQueue.id.desc()
                    ).limit(200)
                )
            ]

    async def get_review_detail(self, rid):
        async with self.sessions() as session:
            row = await session.get(ReviewQueue, rid)
            if row is None:
                raise LookupError("缺口不存在")
            originals = list(
                await session.scalars(
                    select(LowConfidenceQuestion)
                    .where(LowConfidenceQuestion.matched_review_id == rid)
                    .order_by(LowConfidenceQuestion.id)
                )
            )
            return {**record(row), "raw_questions": [record(raw) for raw in originals]}

    async def update_review_status(self, rid, status, approved_answer):
        if status not in {"通过", "驳回"}:
            raise ValueError("非法审核状态")
        async with self.sessions.begin() as session:
            row = await session.get(ReviewQueue, rid, with_for_update=True)
            if row is None:
                raise LookupError("缺口不存在")
            if row.review_status != "待审":
                raise ReviewConflict("只允许处理待审缺口")
            row.review_status, row.approved_answer = status, approved_answer

    async def review_counts(self):
        async with self.sessions() as session:
            rows = dict(
                (
                    await session.execute(
                        select(ReviewQueue.review_status, func.count()).group_by(
                            ReviewQueue.review_status
                        )
                    )
                ).all()
            )
            return {state: rows.get(state, 0) for state in ("待审", "通过", "驳回")}

    async def insert_eval_run(self, triggered_by, dataset_size, metrics):
        if triggered_by not in {"定时", "手动"} or dataset_size < 1:
            raise ValueError("评估轮次参数无效")
        async with self.sessions.begin() as session:
            row = EvalRun(triggered_by=triggered_by, dataset_size=dataset_size, metrics=metrics)
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return record(row)

    async def list_eval_runs(self, limit=10):
        async with self.sessions() as session:
            return [
                record(row)
                for row in await session.scalars(
                    select(EvalRun)
                    .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
                    .limit(limit)
                )
            ]
