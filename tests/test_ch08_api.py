import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.db.models import Ticket, ToolAuditLog


def frames(response):
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]


@pytest.mark.parametrize("confirmed", [True, False])
async def test_sse_ticket_preview_resume_done_and_repeat_is_error(client, model, db, confirmed):
    model.first = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "create_ticket",
                "id": "ticket",
                "args": {"description": "饮水机漏水，需要人工跟进", "ticket_type": "售后"},
            }
        ],
    )
    response = await client.post("/api/chat", json={"user_id": "test", "message": "请建工单"})
    events = frames(response)
    assert response.status_code == 200 and "event: interrupt" in response.text
    card = events[-1]
    assert card["kind"] == "confirm_ticket" and card["preview"]["description"]
    assert not any(e.get("event") == "done" for e in events)
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ToolAuditLog)) == 0
    body = {
        "conversation_id": card["conversation_id"],
        "confirmed": confirmed,
        "interrupt_id": card["interrupt_id"],
    }
    response = await client.post("/api/actions/resume", json=body)
    assert frames(response)[-1] == {"event": "done", "conversation_id": card["conversation_id"]}
    assert "data: [DONE]" in response.text
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Ticket)) == int(confirmed)
    duplicate = await client.post("/api/actions/resume", json=body)
    assert "event: error" in duplicate.text and "invalid_resume" in duplicate.text


async def test_resume_choice_validation_precedes_stream(client):
    for choice in [{}, {"confirmed": "false"}, {"order_id": "1001", "confirmed": True}]:
        response = await client.post("/api/actions/resume", json={"conversation_id": 1, **choice})
        assert response.status_code == 422
