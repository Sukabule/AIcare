import pytest
from pydantic import ValidationError

from app.schemas import AfterSalesTicket, ChatRequest, ExtractRequest


@pytest.mark.parametrize("kind", ["退款", "换货", "维修", "投诉", "其他"])
def test_ticket_enum_and_nullable_order(kind):
    ticket = AfterSalesTicket(order_id=None, request_type=kind, expected_solution="未明确")
    assert ticket.model_dump()["order_id"] is None


def test_unknown_request_type_is_rejected():
    with pytest.raises(ValidationError):
        AfterSalesTicket(order_id="123", request_type="退货退款", expected_solution="退款")


@pytest.mark.parametrize(
    "payload", [{"user_id": " ", "message": "hi"}, {"user_id": "a", "message": " "}]
)
def test_empty_chat_fields_are_rejected(payload):
    with pytest.raises(ValidationError):
        ChatRequest(**payload)


def test_empty_extract_is_rejected():
    with pytest.raises(ValidationError):
        ExtractRequest(text=" ")
