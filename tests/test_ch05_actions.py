from sqlalchemy import func, select

from app.db.models import Ticket


async def test_ticket_action_requires_explicit_valid_fields_and_writes_once(client, repo, db):
    cid = (await repo.get_or_create("u", None)).id
    for body in (
        {"conversation_id": cid, "ticket_type": "", "description": ""},
        {"conversation_id": cid, "ticket_type": "投诉", "description": " "},
    ):
        assert (await client.post("/api/actions/create-ticket", json=body)).status_code == 422
    response = await client.post(
        "/api/actions/create-ticket",
        json={"conversation_id": cid, "ticket_type": "投诉", "description": "物流服务态度不好"},
    )
    assert response.status_code == 200 and response.json()["ticket_no"].startswith("TK")
    async with db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Ticket)) == 1
    assert (
        await client.post(
            "/api/actions/create-ticket",
            json={"conversation_id": 9999999, "ticket_type": "咨询", "description": "咨询问题"},
        )
    ).status_code == 404
