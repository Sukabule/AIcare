from collections import defaultdict
from datetime import UTC, datetime, timedelta


def metrics_query(days):
    now = datetime.now(UTC)
    return dict(
        view="observations",
        dimensions=[{"field": "tags"}, {"field": "traceId"}],
        metrics=[
            {"measure": name, "aggregation": "sum"}
            for name in ("totalTokens", "inputTokens", "outputTokens")
        ],
        filters=[],
        fromTimestamp=(now - timedelta(days=days)).isoformat(),
        toTimestamp=now.isoformat(),
    )


def aggregate_cost(rows):
    groups = defaultdict(
        lambda: {"trace_ids": set(), "tokens": 0, "input_tokens": 0, "output_tokens": 0}
    )
    excluded, excluded_tokens = set(), 0
    for row in rows:
        tags = row.get("tags") or []
        if "usage:invalid" in tags:
            excluded.add(row["traceId"])
            excluded_tokens += int(row.get("sum_totalTokens", 0) or 0)
            continue
        intent = next(
            (tag.removeprefix("intent:") for tag in tags if tag.startswith("intent:")), "未分类"
        )
        group = groups[intent]
        group["trace_ids"].add(row["traceId"])
        group["tokens"] += int(row.get("sum_totalTokens", 0) or 0)
        group["input_tokens"] += int(row.get("sum_inputTokens", 0) or 0)
        group["output_tokens"] += int(row.get("sum_outputTokens", 0) or 0)
    total = sum(group["tokens"] for group in groups.values())
    result = []
    for intent, group in groups.items():
        requests = len(group["trace_ids"])
        result.append(
            dict(
                intent=intent,
                requests=requests,
                total_tokens=group["tokens"],
                input_tokens=group["input_tokens"],
                output_tokens=group["output_tokens"],
                average_tokens=group["tokens"] / requests if requests else 0,
                share=group["tokens"] / total if total else 0,
            )
        )
    return dict(
        rows=sorted(result, key=lambda row: row["total_tokens"], reverse=True),
        total_tokens=total,
        requests=sum(r["requests"] for r in result),
        excluded_traces=len(excluded),
        excluded_reported_tokens=excluded_tokens,
    )
