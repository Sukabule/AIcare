import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from app.config import Settings
from app.core.costs import aggregate_cost
from app.core.observability import Observability
from app.core.rag import RagPipeline


def test_missing_keys_never_create_callback():
    graph = SimpleNamespace(with_config=lambda config: (_ for _ in ()).throw(AssertionError()))
    assert Observability(Settings(_env_file=None)).bind(graph) is graph


def test_cost_aggregation_counts_traces_once_and_uses_trace_intent_tags():
    rows = [
        {"tags": ["intent:物流"], "traceId": "a", "sum_totalTokens": 120},
        {"tags": ["intent:物流", "other"], "traceId": "a", "sum_totalTokens": 30},
        {"tags": ["intent:物流"], "traceId": "b", "sum_totalTokens": 50},
        {"tags": ["intent:商品咨询"], "traceId": "c", "sum_totalTokens": 200},
    ]
    report = aggregate_cost(rows)
    assert report["total_tokens"] == 400
    indexed = {r["intent"]: r for r in report["rows"]}
    assert indexed["物流"]["requests"] == 2 and indexed["物流"]["average_tokens"] == 100
    assert indexed["商品咨询"]["share"] == 0.5


def test_invalid_usage_trace_is_disclosed_and_excluded_from_cost_totals():
    report = aggregate_cost(
        [
            {"tags": ["intent:物流"], "traceId": "good", "sum_totalTokens": 20},
            {
                "tags": ["intent:商品咨询", "usage:invalid"],
                "traceId": "bad",
                "sum_totalTokens": 450,
            },
        ]
    )
    assert report["total_tokens"] == 20 and report["requests"] == 1
    assert report["excluded_traces"] == 1 and report["excluded_reported_tokens"] == 450


async def test_four_signal_gate_precedes_selfcheck_and_reports_correct_source():
    calls = []

    async def check(query, hits):
        calls.append(query)
        return False, "资料不足"

    rag = RagPipeline(None, None, check, evidence_threshold=0.6)
    assert (await rag.assess("问题", [{"score": 0.1}]))[0] == "retrieval_low_conf"
    assert calls == []
    assert (await rag.assess("问题", [{"score": 0.9}]))[0] == "self_check"
    assert calls == ["问题"]


async def test_overview_reports_independent_dependencies(client, application, tmp_path):
    application.state.ch09_report_dir = tmp_path
    response = (await client.get("/api/observability/overview")).json()
    assert not response["cost"]["present"] and not response["calibration"]["present"]
    assert not response["trend"]["present"] and response["trend"]["status"] == "empty"


async def test_intent_uses_sdk4_valid_wire_types_and_current_request_trace():
    events = []

    def batch(*, batch, request_options):
        events.extend(batch)

    obs = Observability(Settings(_env_file=None))
    obs.client = SimpleNamespace(
        get_current_trace_id=lambda: "1" * 32,
        api=SimpleNamespace(ingestion=SimpleNamespace(batch=batch)),
    )
    assert await obs.intent("物流", 0.91, 123) == "1" * 32
    event = events[0]
    assert isinstance(event.timestamp, str)
    assert event.body.id == "1" * 32 and event.body.session_id == "123"
    assert event.body.metadata == {"intent": "物流", "intent_confidence": 0.91}
    assert event.body.tags == ["intent:物流"]


def test_disabled_request_scope_never_initializes_sdk():
    obs = Observability(Settings(_env_file=None))
    with obs.request_scope(123) as trace_id:
        assert trace_id is None
    with obs.summary_scope(123, None) as config:
        assert config is None


def test_observation_setup_failure_does_not_abort_customer_request():
    def broken(**kwargs):
        raise RuntimeError("tracer unavailable")

    obs = Observability(Settings(_env_file=None))
    obs.client = SimpleNamespace(start_as_current_observation=broken)
    with obs.request_scope(123) as trace_id:
        assert trace_id is None


async def test_root_input_output_are_request_local_and_failure_is_recorded():
    spans = []

    class Span:
        trace_id = "1" * 32

        def __init__(self):
            self.observation, self.trace = {}, {}

        def update(self, **kwargs):
            self.observation.update(kwargs)

        def set_trace_io(self, **kwargs):
            self.trace.update(kwargs)

    @contextmanager
    def start(**kwargs):
        span = Span()
        spans.append(span)
        yield span

    obs = Observability(Settings(_env_file=None))
    obs.client = SimpleNamespace(start_as_current_observation=start)

    async def turn(cid):
        payload = {"question": f"问题{cid}", "conversation_id": cid}
        with obs.request_scope(cid, input=payload):
            await asyncio.sleep(0)
            obs.record_output(f"回答{cid}")

    await asyncio.gather(turn(1), turn(2))
    for span in spans:
        cid = span.trace["input"]["conversation_id"]
        assert span.trace["output"] == span.observation["output"] == f"回答{cid}"
    obs.record_output("作用域之外不能覆盖上轮输出")
    assert spans[-1].trace["output"] == "回答2"
    try:
        with obs.request_scope(3, input={"question": "失败"}):
            raise ValueError("business error")
    except ValueError:
        pass
    else:
        raise AssertionError("业务错误必须继续上抛")
    assert spans[-1].observation["level"] == "ERROR"
    assert spans[-1].trace["output"] == {"error": "ValueError"}
