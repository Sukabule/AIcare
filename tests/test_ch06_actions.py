import json

from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.db.models import Ticket
from app.schemas import ChatRequest
from tests.test_ch06_workflow import fixture_runtime


async def test_resume_api_frames_pause_and_finish(client, application, repo, db, tmp_path):
    graph, _, _ = await fixture_runtime(
        repo, db, tmp_path, [AIMessage(content="不能退，请联系人工")]
    )
    application.state.agent_factory = lambda: graph
    try:
        response = await client.post("/api/chat", json={"user_id": "u", "message": "我要退款"})
        assert "event: interrupt\n" in response.text and "data: [DONE]" in response.text
        assert '"event": "done"' not in response.text
        payload = next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: {") and '"interrupt"' in line
        )
        body = {
            "conversation_id": payload["conversation_id"],
            "order_id": payload["orders"][0]["order_id"],
            "interrupt_id": payload["interrupt_id"],
        }
        bad = await client.post("/api/actions/resume", json=dict(body, order_id="999999"))
        assert "event: error" in bad.text and "invalid_resume" in bad.text
        good = await client.post("/api/actions/resume", json=body)
        assert '"delta": "不能退，请联系人工"' in good.text and '"event": "done"' in good.text
        repeated = await client.post("/api/actions/resume", json=body)
        assert "invalid_resume" in repeated.text
        assert (
            await client.post("/api/actions/resume", json={"conversation_id": 1})
        ).status_code == 422
    finally:
        await graph.close()


def test_ticket_enum_keeps_previous_values_and_accepts_refund():
    assert set(Ticket.__table__.c.ticket_type.type.enums) == {
        "售后",
        "投诉",
        "咨询",
        "其他",
        "退款",
    }


async def test_refund_requires_proposal_and_fixed_reason_then_writes_once(
    client, application, repo, db, tmp_path
):
    ai = AIMessage(
        content="", tool_calls=[{"name": "submit_refund", "args": {"order_id": "1001"}, "id": "r"}]
    )
    graph, _, _ = await fixture_runtime(repo, db, tmp_path, [ai, AIMessage(content="请填写申请表")])
    application.state.agent_factory = lambda: graph
    try:
        cid = (await repo.get_or_create("u", None)).id
        body = {"conversation_id": cid, "order_id": "1001", "reason": "质量问题"}
        assert (await client.post("/api/actions/create-refund", json=body)).status_code == 409
        await graph.run_agent_turn(
            ChatRequest(user_id="u", message="订单1001能退吗", conversation_id=cid)
        )
        # Closing or cancelling the form sends no request and creates no ticket.
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Ticket)) == 0
        assert (
            await client.post("/api/actions/create-refund", json=dict(body, reason="随便退款"))
        ).status_code == 422
        assert (
            await client.post("/api/actions/create-refund", json=dict(body, order_id="1002"))
        ).status_code == 409
        response = await client.post("/api/actions/create-refund", json=body)
        assert response.status_code == 200 and response.json()["ticket_type"] == "退款"
        assert (await client.post("/api/actions/create-refund", json=body)).status_code == 409
        async with db.sessions() as session:
            rows = list(await session.scalars(select(Ticket)))
            assert len(rows) == 1 and rows[0].ticket_type == "退款"
            assert "1001" in rows[0].description and "质量问题" in rows[0].description
    finally:
        await graph.close()
