import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from test_ch05_graph import SequenceModel, call, runtime

from app.db.models import Ticket, ToolAuditLog
from app.graph.runtime import ResumeConflict
from app.schemas import ChatRequest
from app.schemas.actions import ResumeRequest


@pytest.mark.parametrize("confirmed", [True, False])
async def test_interrupt_before_any_tool_confirm_cancel_and_duplicate_resume(
    repo, db, tmp_path, confirmed
):
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "query_order", "args": {"order_id": "1001"}, "id": "read"},
            {
                "name": "create_ticket",
                "args": {"description": "猫砂盆漏电，需要人工跟进", "ticket_type": "售后"},
                "id": "ticket",
            },
            {
                "name": "create_ticket",
                "args": {"description": "第二个单", "ticket_type": "售后"},
                "id": "extra",
            },
        ],
    )
    model = SequenceModel([ai, AIMessage(content="处理结果")])
    graph = await runtime(repo, db, tmp_path, "人工", model)
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="u", message="请建工单跟进漏电"))
        card = result.interrupt
        assert card["kind"] == "confirm_ticket" and card["preview"]["ticket_type"] == "售后"
        async with db.sessions() as s:
            assert await s.scalar(select(func.count()).select_from(ToolAuditLog)) == 0
            assert await s.scalar(select(func.count()).select_from(Ticket)) == 0
        with pytest.raises(ResumeConflict):
            await graph.resume_turn(result.conversation_id, "1001")
        with pytest.raises(ResumeConflict):
            await graph.resume_turn(result.conversation_id, confirmed=confirmed, interrupt_id="old")
        # Checkpoint survives runtime recreation, including pending confirmation.
        await graph.close()
        graph = await runtime(repo, db, tmp_path, "人工", model)
        result = await graph.resume_turn(
            result.conversation_id, confirmed=confirmed, interrupt_id=card["interrupt_id"]
        )
        assert len(model.tools) == 1  # next response unbound, no repeated write proposal
        assert not result.interrupt
        async with db.sessions() as s:
            assert await s.scalar(select(func.count()).select_from(Ticket)) == int(confirmed)
            audits = list(await s.scalars(select(ToolAuditLog).order_by(ToolAuditLog.id)))
            assert len(audits) == 3
            ticket = next(a for a in audits if a.tool_call_id == "ticket")
            assert ticket.status == ("成功" if confirmed else "权限拒绝")
            assert ticket.retry_count == 0
        extra = next(r for r in result.tool_results if r.tool_call_id == "extra")
        assert not extra.ok and "一次" in extra.error["message"]
        with pytest.raises(ResumeConflict):
            await graph.resume_turn(result.conversation_id, confirmed=True)
    finally:
        await graph.close()


async def test_invalid_ticket_does_not_pause_and_is_audited(repo, db, tmp_path):
    model = SequenceModel(
        [call("create_ticket", {"ticket_type": "售后"}, "bad"), AIMessage(content="请描述问题")]
    )
    graph = await runtime(repo, db, tmp_path, "人工", model)
    try:
        result = await graph.run_agent_turn(ChatRequest(user_id="u", message="建工单"))
        assert not result.interrupt and not result.tool_results[0].ok
        async with db.sessions() as s:
            audit = await s.scalar(select(ToolAuditLog))
            assert audit.status == "校验拦下"
    finally:
        await graph.close()


def test_resume_schema_requires_one_strict_choice():
    assert ResumeRequest(conversation_id=1, confirmed=False).confirmed is False
    for body in [{}, {"order_id": "1001", "confirmed": True}, {"confirmed": "false"}]:
        with pytest.raises(ValueError):
            ResumeRequest(conversation_id=1, **body)
