import pytest
from pydantic import ValidationError

from app.core.intent import IntentResult, classify
from app.core.query_understanding import ExpandedQueries, merge_policy_hits
from app.core.rag import RagPipeline
from app.graph.routing import route_by_intent
from app.tools.builtin.orders import query_order
from app.tools.business import extract_order_id, list_user_orders


def test_eight_intents_five_routes():
    expected = {
        "物流": "business",
        "订单": "business",
        "商品咨询": "knowledge",
        "退款退货": "refund_flow",
        "售后": "refund_flow",
        "投诉": "escalate",
        "闲聊": "fallback_script",
        "其他": "fallback_script",
    }
    assert {i: route_by_intent({"intent": i}) for i in expected} == expected


@pytest.mark.parametrize(
    "value",
    [
        {"intent": "不确定", "confidence": 0.8},
        {"intent": "订单", "confidence": 1.1},
        {"intent": "订单", "confidence": True},
        {"intent": "订单", "confidence": "0.8"},
    ],
)
def test_intent_schema_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        IntentResult.model_validate(value)


async def test_invalid_intent_response_defaults_to_other_without_hiding_transport_errors():
    class Model:
        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            return {"intent": "无此类别", "confidence": 2}

    assert (await classify(Model(), "怪问题", [])).intent == "其他"

    class Broken(Model):
        async def ainvoke(self, messages):
            raise ConnectionError("upstream failed")

    with pytest.raises(ConnectionError):
        await classify(Broken(), "退款", [])


@pytest.mark.parametrize(
    "query,expected",
    [
        ("订单1001可以退吗", "1001"),
        ("订单号 MH20260701123 申请退款", "MH20260701123"),
        ("退款要7天，金额99元", None),
        ("物流单号 SF12345", None),
        ("尾号 20260701 那单能退吗", "20260701"),
    ],
)
def test_order_extraction_does_not_take_policy_numbers_or_tracking(query, expected):
    assert extract_order_id(query) == expected


async def test_order_cards_match_existing_tool_and_are_stable():
    orders = list_user_orders("user-a")
    assert orders == list_user_orders("user-a")
    assert len({o["order_id"] for o in orders}) == len(orders) == 3
    for order in orders:
        data = await query_order.ainvoke({"order_id": order["order_id"]})
        assert all(order[k] == data[k] for k in ("order_id", "product_name", "amount", "status"))


def test_exact_three_distinct_queries_and_stable_hit_deduplication():
    for values in (["a", "b"], ["a", "b", "c", "d"], ["a", "a", "b"], ["", "b", "c"]):
        with pytest.raises(ValidationError):
            ExpandedQueries(queries=values)
    merged = merge_policy_hits(
        [
            [{"id": 1, "score": 0.8}, {"id": 2, "score": 0.6}],
            [{"id": 2, "score": 0.9}, {"id": 3, "score": 0.7}],
            [{"id": 1, "score": 0.5}],
        ]
    )
    assert [h["id"] for h in merged] == [2, 1, 3]
    assert merged[0]["score"] == 0.9


async def test_policy_gate_checks_rules_not_missing_customer_facts():
    calls = []

    class Check:
        async def __call__(self, query, hits):
            raise AssertionError("Refund policy gate must leave user clarification to the agent")

        async def check_policy(self, query, hits):
            calls.append(query)
            return True, "具备政策条件，用户签收状态由客服追问"

    rag = RagPipeline(None, None, Check(), threshold=0.5)
    assert (await rag.assess("这个能退吗", [{"score": 0.9}], policy_only=True))[0] is None
    assert len(calls) == 1
    assert (await rag.assess("能退吗", [{"score": 0.1}], policy_only=True))[
        0
    ] == "retrieval_low_conf"
    assert len(calls) == 1
