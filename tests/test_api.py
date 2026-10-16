import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.models import Ticket
from app.schemas import AfterSalesTicket


async def test_sse_tool_delta_done_framing_and_continuation(client, model):
    model.first = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "query_logistics",
                "args": {"tracking_no": "SF1001"},
                "id": "call1",
                "type": "tool_call",
            }
        ],
    )
    response = await client.post("/api/chat", json={"user_id": "u1", "message": "查物流"})
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = response.text.strip().split("\n\n")
    assert frames[-1] == "data: [DONE]"
    events = [json.loads(frame[6:]) for frame in frames[:-1]]
    assert events[0] == {"event": "tool", "name": "query_logistics"}
    assert events[1] == {"delta": "工具核验后的答复"}
    cid = events[-1]["conversation_id"]
    model.first = AIMessage(content="第二轮")
    result = await client.post(
        "/api/agent", json={"user_id": "u1", "message": "继续", "conversation_id": cid}
    )
    assert result.status_code == 200 and result.json()["conversation_id"] == cid
    assert any(m.content == "工具核验后的答复" for m in model.calls[-1][1])
    assert any(m.type == "tool" for m in model.calls[-1][1])


@pytest.mark.parametrize("path,status", [("/api/chat", 200), ("/api/agent", 404)])
async def test_unknown_conversation(client, path, status):
    response = await client.post(
        path, json={"user_id": "u1", "message": "hi", "conversation_id": 99999999}
    )
    assert response.status_code == status
    assert "conversation_not_found" in response.text
    if path.endswith("chat"):
        assert response.text.startswith("event: error\ndata: ")
        assert "[DONE]" not in response.text


async def test_complaint_sse_actions_and_continuing_without_click(client, application, model, db):
    service = application.state.agent_factory()

    async def classify(query):
        return "投诉" if "投诉" in query else "订单"

    service.nodes.classifier = classify
    response = await client.post("/api/chat", json={"user_id": "u", "message": "我要投诉"})
    frames = response.text.strip().split("\n\n")
    events = [json.loads(frame[6:]) for frame in frames[:-1]]
    assert [e.get("event", "delta") for e in events] == ["delta", "actions", "done"]
    assert [a["type"] for a in events[1]["items"]] == ["transfer_human", "create_ticket"]
    assert not model.calls
    cid = events[-1]["conversation_id"]
    result = await client.post(
        "/api/agent", json={"user_id": "u", "message": "查订单", "conversation_id": cid}
    )
    assert result.status_code == 200
    assert result.json()["suggested_actions"] == []
    assert result.json()["trace"]["route"] == "business"
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Ticket)) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": " ", "message": "hi"},
        {"user_id": "u1", "message": " "},
        {"user_id": "u1", "message": "hi", "conversation_id": -1},
        {"user_id": "u1", "message": "hi", "conversation_id": "12"},
        {"session_id": "legacy", "message": "hi"},
    ],
)
async def test_request_validation(client, body):
    assert (await client.post("/api/chat", json=body)).status_code == 422


async def test_model_error_sse_and_json(client, model):
    model.error = RuntimeError("private key or upstream details")
    response = await client.post("/api/chat", json={"user_id": "u1", "message": "hi"})
    assert "event: error" in response.text and "private" not in response.text
    response = await client.post("/api/agent", json={"user_id": "u1", "message": "hi"})
    assert response.status_code == 502


async def test_database_error_sse_and_json(client, application):
    class FailedService:
        async def stream_agent_turn(self, body):
            raise OperationalError("sql", {}, RuntimeError("private"))
            yield

        async def run_agent_turn(self, body):
            raise OperationalError("sql", {}, RuntimeError("private"))

    application.state.agent_factory = lambda: FailedService()
    stream = await client.post("/api/chat", json={"user_id": "u1", "message": "hi"})
    assert "database_unavailable" in stream.text and "private" not in stream.text
    result = await client.post("/api/agent", json={"user_id": "u1", "message": "hi"})
    assert result.status_code == 503


async def test_extract_remains_unchanged(client, application):
    class ExtractModel:
        def with_structured_output(self, schema, **options):
            assert schema is AfterSalesTicket
            assert options == {"method": "json_schema", "strict": True}
            return self

        async def ainvoke(self, messages):
            return AfterSalesTicket(
                order_id=None, request_type="退款", expected_solution="要求退款"
            )

    application.state.llm = ExtractModel()
    response = await client.post("/api/extract", json={"text": "我要退款"})
    assert response.status_code == 200
    assert response.json() == {
        "order_id": None,
        "request_type": "退款",
        "expected_solution": "要求退款",
    }


async def test_extract_failure_returns_502(client, application):
    class Broken:
        def with_structured_output(self, *args, **kwargs):
            raise RuntimeError("private")

    application.state.llm = Broken()
    response = await client.post("/api/extract", json={"text": "退款"})
    assert response.status_code == 502 and "private" not in response.text
