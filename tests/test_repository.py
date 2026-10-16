import pytest

from app.db.repository import ConversationNotFound


async def test_conversation_identity_messages_and_nullable_json(repo):
    conversation = await repo.get_or_create("u1", None)
    assert conversation.id > 0 and conversation.status == "进行中"
    assert (await repo.get_or_create("u1", conversation.id)).id == conversation.id
    with pytest.raises(ConversationNotFound):
        await repo.get_or_create("u2", conversation.id)
    with pytest.raises(ConversationNotFound):
        await repo.get_or_create("u1", 99999999)
    await repo.append(conversation.id, "user", "你好")
    calls = [{"name": "query_order", "args": {"order_id": "1001"}, "id": "c1", "type": "tool_call"}]
    await repo.append(conversation.id, "assistant", None, tool_calls=calls)
    await repo.append(conversation.id, "tool", '{"ok":true}', tool_call_id="c1")
    rows = await repo.history(conversation.id)
    assert [row.role for row in rows] == ["user", "assistant", "tool"]
    assert rows[0].tool_calls is None
    assert rows[1].tool_calls == calls and rows[1].content is None
    assert rows[2].tool_call_id == "c1"


async def test_faq_literal_like_miss_and_wildcard_escape(repo):
    assert "7 天" in (await repo.search_faq("退货"))[0]["answer"]
    assert "8 元" in (await repo.search_faq("运费"))[0]["answer"]
    assert await repo.search_faq("邮费") == []
    assert await repo.search_faq("%") == []
    assert await repo.search_faq("_") == []


async def test_ticket_and_handoff_commit_together(repo):
    conversation = await repo.get_or_create("u1", None)
    ticket = await repo.create_ticket(conversation.id, "商品损坏，需要人工处理", "售后")
    assert ticket.ticket_no.startswith("TK")
    assert ticket.status == "待处理"
    assert (await repo.get_or_create("u1", conversation.id)).status == "已转人工"
    with pytest.raises(ConversationNotFound):
        await repo.create_ticket(99999999, "invalid", "售后")
