from app.core.evaluation import UpstreamCircuit


def test_balance_failure_stops_remaining_calls_and_transient_error_does_not():
    class UpstreamError(Exception):
        def __init__(self, status):
            self.status_code = status

    circuit = UpstreamCircuit()
    circuit.record(UpstreamError(502))
    assert circuit.reason is None
    circuit.record(UpstreamError(402))
    assert circuit.reason == "balance_insufficient"
    circuit.record(UpstreamError(502))
    assert circuit.reason == "balance_insufficient"
