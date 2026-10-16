import pytest

from app.db.repository import ConversationNotFound


async def test_summary_segments_monotonic_projection_and_ranges(repo):
    conv = await repo.get_or_create("summary-user", None)
    rows = [
        await repo.append(conv.id, "user" if i % 2 == 0 else "assistant", str(i)) for i in range(8)
    ]
    assert await repo.count_messages_after(conv.id, rows[3].id) == 4
    assert len(await repo.list_dialog_messages(conv.id)) == 8
    for i in range(4):
        assert (
            await repo.append_summary_segment(
                conv.id, rows[i * 2].id, rows[i * 2 + 1].id, f"摘要{i}"
            )
            == i + 1
        )
    current = await repo.get_or_create("summary-user", conv.id)
    assert current.summary == "摘要1\n摘要2\n摘要3"
    assert current.summary_upto_msg_id == rows[-1].id
    assert await repo.list_summary_segments(conv.id, 2) == ["摘要2", "摘要3"]
    assert len(await repo.list_summary_segments(conv.id)) == 4
    with pytest.raises(ValueError):
        await repo.append_summary_segment(conv.id, rows[0].id, rows[1].id, "重复")
    await repo.update_layer1_from(conv.id, rows[-1].id)
    await repo.update_layer1_from(conv.id, rows[0].id)
    assert (await repo.get_or_create("summary-user", conv.id)).layer1_from_msg_id == rows[-1].id
    listing = await repo.list_conversations("summary-user")
    assert listing[0]["has_summary"] and listing[0]["preview"] == "0"
    assert await repo.list_conversations("other-user") == []


async def test_projection_update_and_dialog_filter(repo):
    conv = await repo.get_or_create("u1", None)
    a = await repo.append(conv.id, "user", "订单1001")
    await repo.append(conv.id, "tool", "结果", tool_call_id="x")
    assert len(await repo.list_dialog_messages(conv.id)) == 1
    await repo.update_conversation_summary(conv.id, "摘要", a.id)
    assert (await repo.get_or_create("u1", conv.id)).summary == "摘要"
    with pytest.raises(ConversationNotFound):
        await repo.get_conversation(999999999)


async def test_conversation_read_apis(client, repo):
    conv = await repo.get_or_create("u1", None)
    await repo.append(conv.id, "user", "我的订单")
    await repo.append(conv.id, "assistant", "原文答复")
    await repo.append(conv.id, "tool", "工具", tool_call_id="c")
    response = await client.get("/api/conversations", params={"user_id": "u1"})
    assert response.status_code == 200 and response.json()[0]["id"] == conv.id
    response = await client.get(f"/api/conversations/{conv.id}/messages")
    assert [m["content"] for m in response.json()] == ["我的订单", "原文答复"]
    assert (await client.get("/api/conversations/999999999/messages")).status_code == 404
    assert (await client.get("/api/conversations")).status_code == 422
