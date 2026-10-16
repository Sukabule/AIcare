import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.flywheel import Flywheel, NormalizedGap
from app.db.models import LowConfidenceQuestion
from app.db.rag_repository import RagRepository


class Model:
    def __init__(self, replies):
        self.replies, self.inputs = list(replies), []

    def with_structured_output(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.inputs.append(messages)
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


async def raw(repo, text):
    cid = (await repo.get_or_create("flywheel", None)).id
    return await RagRepository(repo.sessions).low_confidence(cid, text, "user_feedback", "未解决")


async def test_serial_same_batch_merge_and_rerun_is_noop(repo, db):
    await raw(repo, "邮费多少钱")
    await raw(repo, "运费怎么算")
    model = Model(
        [
            {
                "normalized_question": "配送运费如何计算",
                "matched_question_id": None,
                "ai_suggested_answer": "需要审核",
            }
        ]
    )
    wheel = Flywheel(repo, model)
    await wheel.process_pending(1)
    queued = await repo.list_review_queue()
    rid = queued[0]["id"]
    model.replies.append(
        {
            "normalized_question": "配送费用是多少",
            "matched_question_id": rid,
            "ai_suggested_answer": "待核实",
        }
    )
    report = await wheel.process_pending(10)
    assert report["merged"] == 1 and len(model.inputs) == 2
    assert str(rid) in model.inputs[-1][-1].text
    detail = await repo.get_review_detail(rid)
    assert detail["occurrence_count"] == 2 and len(detail["raw_questions"]) == 2
    assert (await wheel.process_pending(10))["processed"] == 0
    async with db.sessions() as session:
        assert all(
            r.matched_review_id == rid for r in await session.scalars(select(LowConfidenceQuestion))
        )


@pytest.mark.parametrize("final_status", ["驳回", "通过"])
async def test_matches_finalized_rows_never_revive_hallucination_bad_json_stay_pending(
    repo, final_status
):
    item = await repo.insert_review_item("退货邮费由谁承担", "未核准")
    await repo.update_review_status(
        item["id"], final_status, "核准答案" if final_status == "通过" else None
    )
    await raw(repo, "退回邮费谁付")
    await raw(repo, "没有候选的另一问题")
    await raw(repo, "坏输出问题")
    model = Model(
        [
            {
                "normalized_question": "退货邮费由谁承担",
                "matched_question_id": item["id"],
                "ai_suggested_answer": "待核实",
            },
            {
                "normalized_question": "新问题",
                "matched_question_id": 999999,
                "ai_suggested_answer": "待核实",
            },
            ValueError("bad JSON"),
        ]
    )
    report = await Flywheel(repo, model).process_pending(10)
    assert report["merged"] == 1 and report["skipped"] == 2
    detail = await repo.get_review_detail(item["id"])
    assert detail["review_status"] == final_status and detail["occurrence_count"] == 2
    assert len(await repo.fetch_unmatched_low_conf(10)) == 2


@pytest.mark.parametrize("question", ["贵", "滤", "    "])
def test_normalization_rejects_truncated_or_blank_question(question):
    with pytest.raises(ValidationError):
        NormalizedGap(
            normalized_question=question,
            matched_question_id=None,
            ai_suggested_answer="需要人工核实",
        )
