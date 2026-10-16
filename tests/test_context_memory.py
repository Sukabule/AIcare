from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.config import Settings
from app.core.budget import compute
from app.core.memory import build_window, layer_tokens, next_layer1_from, to_layer2


def turns():
    return [
        HumanMessage(content="订单1001", id="db-1"),
        AIMessage(content="第一轮答复" * 30),
        HumanMessage(content="物流", id="db-3"),
        AIMessage(
            content="正在查询",
            tool_calls=[{"name": "query_logistics", "args": {"order_id": "1001"}, "id": "c1"}],
        ),
        ToolMessage(content="大结果" * 100, tool_call_id="c1", name="query_logistics"),
        AIMessage(content="第二轮答复" * 30),
        HumanMessage(content="现在呢", id="db-7"),
    ]


def chars(rows):
    return sum(len(m.text) for m in rows)


def test_window_layers_keep_original_state_and_tool_structure():
    rows = turns()
    result = build_window(rows, 2, 6, token_counter=chars, tool_keep_tokens=200)
    assert result[0].id == "db-3"
    assert result[1].tool_calls == rows[3].tool_calls
    assert result[2].content == "(已调用 query_logistics,结果从略)"
    assert result[3].content.endswith("…(略)")
    assert result[-1] == rows[-1]
    assert rows[4].content == "大结果" * 100


def test_missing_anchors_falls_back_to_original_and_budget():
    rows = [
        HumanMessage(content="旧问题"),
        AIMessage(content="旧回答"),
        HumanMessage(content="新问题"),
        AIMessage(content="新回答"),
    ]
    assert build_window(rows, 100, 200) == rows
    result = build_window(rows, 100, 200, max_tokens=2, token_counter=len)
    assert result == rows[2:]


def test_trim_never_orphans_tool_messages():
    rows = turns()
    result = build_window(rows, 0, max_tokens=3, token_counter=len)
    assert result == rows[-1:]
    assert rows[0].id == "db-1"


def test_abandoned_tool_call_is_closed_in_model_copy_only():
    call = {"name": "query_order", "args": {"order_id": "1001"}, "id": "lost"}
    rows = [
        HumanMessage(content="查订单", id="db-1"),
        AIMessage(content="", tool_calls=[call]),
        HumanMessage(content="再问一次", id="db-3"),
    ]
    window = build_window(rows)
    assert len(window) == 4
    assert window[2].tool_call_id == "lost" and "未完成" in window[2].content
    assert len(rows) == 3


def test_half_compression_small_results_unchanged():
    rows = turns()
    rows[4] = ToolMessage(content="已发货", tool_call_id="c1", name="query_logistics")
    compressed = to_layer2(rows, token_counter=chars)
    assert compressed[4] == rows[4]
    assert compressed[0] == rows[0]


def test_settlement_boundary_excludes_first_kept_user():
    rows = turns()
    boundary = next_layer1_from(rows, 0, 1, token_counter=len)
    assert boundary == 6
    assert layer_tokens(rows, 0, boundary, token_counter=len) == (6, 1)
    assert build_window(rows, boundary, boundary) == rows[-1:]


def test_budget_deducts_fixed_and_peak_and_allows_override():
    settings = Settings(_env_file=None, model_context_window=100000)
    budget = compute(settings)
    fixed = 700 + 5 * 160 + 3 * 250 + 10000 + 1000
    peak = 2000 + 6 * (3000 + 500)
    assert budget.sliding == 100000 - fixed - peak
    assert budget.layer1 + budget.layer2 == budget.sliding
    assert budget.healthy
    assert compute(settings.model_copy(update={"context_window_max_tokens": 800})).sliding == 800
    assert not compute(settings.model_copy(update={"model_context_window": 1000})).healthy
    large = compute(settings.model_copy(update={"model_context_window": 1000000}))
    assert large.sliding == 20 * (2000 + 10000 + 6 * 200)
