import pytest
from sqlalchemy import select

from app.core.rag import RagPipeline
from app.db.models import LowConfidenceQuestion
from app.db.rag_repository import RagRepository


class Retriever:
    def __init__(self, hits):
        self.hits = hits

    async def search_knowledge(self, *args, **kwargs):
        return self.hits


async def test_low_gate_persists_raw_question_without_self_check(db, repo):
    conversation = await repo.get_or_create("rag-test", None)

    async def forbidden(*args):
        raise AssertionError("must not self-check below gate")

    pipeline = RagPipeline(Retriever([{"score": 0.1}]), RagRepository(db.sessions), forbidden, 0.5)
    result = await pipeline.answer_evidence("rewritten", conversation.id, "原始问题")
    assert result["refuse"] and not result["citations"]
    async with db.sessions() as session:
        row = await session.scalar(select(LowConfidenceQuestion))
        assert row.raw_question == "原始问题"
        assert row.source == "retrieval_low_conf"


async def test_semantic_gate_and_sufficient_evidence(db, repo):
    conversation = await repo.get_or_create("rag-test", None)
    hits = [{"id": 1, "score": 0.9, "question": "运费", "answer": "10元", "section_path": "运费"}]

    async def insufficient(*args):
        return False, "没有说明国际配送"

    pipeline = RagPipeline(Retriever(hits), RagRepository(db.sessions), insufficient, 0.5)
    assert (await pipeline.answer_evidence("国际邮费", conversation.id))["refuse"]

    async def sufficient(*args):
        return True, "包含运费"

    pipeline.self_check = sufficient
    result = await pipeline.answer_evidence("运费多少", conversation.id)
    assert not result["refuse"] and result["citations"][0]["number"] == 1


async def test_case_recurrence_and_required_resolution(db):
    repository = RagRepository(db.sessions)
    case = dict(
        eval_id="B1",
        bucket="B_model",
        query="q",
        strategy="hybrid_rerank",
        answer="a",
        reason="unsupported",
        citations=[],
        judge_model="fake",
    )
    await repository.upsert_case(case)
    rows = await repository.cases()
    cid = rows["items"][0]["id"]
    with pytest.raises(ValueError):
        await repository.resolve(cid, "已解决", "  ")
    await repository.resolve(cid, "已解决", "已修正提示词")
    await repository.upsert_case(case)
    row = (await repository.cases())["items"][0]
    assert row["seen_count"] == 2 and row["status"] == "未解决"
