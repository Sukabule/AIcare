"""v3 observations Metrics API, grouped by trace tags and traceId."""

import argparse
import json
from datetime import UTC, datetime

from ch09_common import REPORTS

from app.config import Settings
from app.core.costs import aggregate_cost, metrics_query
from app.core.evaluation import write_report


def run(days=7):
    from langfuse import Langfuse

    settings = Settings()
    if not all(
        (
            settings.langfuse_public_key,
            settings.langfuse_secret_key.get_secret_value(),
            settings.langfuse_base_url,
        )
    ):
        raise RuntimeError("请先配置本地 Langfuse 的三个连接项")
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_base_url,
        tracing_enabled=False,
    )
    response = client.api.legacy.metrics_v1.metrics(
        query=json.dumps(metrics_query(days)),
        request_options={"timeout_in_seconds": 30, "max_retries": 0},
    )
    report = {
        **aggregate_cost(response.data),
        "days": days,
        "updated_at": datetime.now(UTC).isoformat(),
        "source": "Langfuse v3 observations Metrics API",
    }
    write_report(REPORTS / "cost_by_intent.json", report)
    lines = ["意图\t请求数\t总tokens\t平均tokens\t占比"]
    lines.extend(
        f"{r['intent']}\t{r['requests']}\t{r['total_tokens']}\t{r['average_tokens']:.0f}\t{r['share']:.1%}"
        for r in report["rows"]
    )
    if report["excluded_traces"]:
        lines.append(
            f"已排除 {report['excluded_traces']} 条标记 usage:invalid 的历史统计异常 trace；"
            "原记录仍保留。"
        )
    text = "\n".join(lines) + "\n"
    (REPORTS / "cost_by_intent.txt").write_text(text, encoding="utf-8")
    print(text)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.days <= 365:
        parser.error("days 必须在 1-365 范围")
    run(args.days)
