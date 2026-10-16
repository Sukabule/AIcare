from sqlalchemy import select

from app.db.models import ToolAuditLog


async def test_audit_no_fk_accepts_missing_conversation_with_all_fields(repo, db):
    assert not ToolAuditLog.__table__.foreign_keys
    row = await repo.insert_tool_audit(
        conversation_id=999999,
        tool_call_id="blocked",
        tool_name="create_ticket",
        tool_source="builtin",
        mcp_server=None,
        arguments={"description": "退款未到账"},
        result_summary="未确认，不执行",
        status="权限拒绝",
        error_message="需要确认",
        retry_count=0,
        duration_ms=2,
    )
    async with db.sessions() as session:
        saved = await session.scalar(select(ToolAuditLog).where(ToolAuditLog.id == row.id))
        assert saved.conversation_id == 999999 and saved.arguments["description"] == "退款未到账"
        assert saved.created_at and saved.retry_count == 0
