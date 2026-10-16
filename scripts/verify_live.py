"""Run labeled examples against the running service and print outputs for human review."""

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx


def chat(client: httpx.Client, user_id: str, message: str, conversation_id=None) -> tuple[str, int]:
    parts = []
    done = False
    completed_conversation = None
    event = "message"
    started = time.monotonic()
    with client.stream(
        "POST",
        "/api/chat",
        json={"user_id": user_id, "message": message, "conversation_id": conversation_id},
    ) as r:
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("text/event-stream"):
            raise ValueError("Chat response is not SSE")
        for line in r.iter_lines():
            if not line:
                event = "message"
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
                if event == "error":
                    raise ValueError(f"SSE error: {data}")
                if data == "[DONE]":
                    done = True
                    break
                payload = json.loads(data)
                if payload.get("event") == "tool":
                    print("  工具:", payload["name"], flush=True)
                    continue
                if payload.get("event") == "done":
                    completed_conversation = payload["conversation_id"]
                    continue
                delta = payload["delta"]
                parts.append(delta)
                print(f"  +{time.monotonic() - started:.2f}s {delta!r}", flush=True)
    if not done or not parts or completed_conversation is None:
        raise ValueError("Missing deltas or [DONE]")
    return "".join(parts), completed_conversation


def verify(base_url: str) -> bool:
    samples = json.loads(
        (Path(__file__).resolve().parents[1] / "tests/fixtures/after_sales.json").read_text("utf-8")
    )
    all_ok = True
    with httpx.Client(base_url=base_url, timeout=90, trust_env=False) as client:
        for sample in samples:
            response = client.post("/api/extract", json={"text": sample["text"]})
            response.raise_for_status()
            actual = response.json()
            expected = sample["expected"]
            ok = (
                set(actual) == set(expected)
                and actual["order_id"] == expected["order_id"]
                and actual["request_type"] == expected["request_type"]
                and isinstance(actual["expected_solution"], str)
                and all(
                    any(word in actual["expected_solution"] for word in alternatives)
                    for alternatives in sample["solution_keywords"]
                )
            )
            all_ok &= ok
            print(f"[{sample['id']}] {'PASS' if ok else 'FAIL'}")
            print("  标注:", json.dumps(expected, ensure_ascii=False))
            print("  实际:", json.dumps(actual, ensure_ascii=False))
        session_id = f"verify-{uuid4().hex}"
        order_id = f"MH{uuid4().hex[:12].upper()}"
        print("\n第一轮（显示每个增量的到达时间）：")
        _, conversation_id = chat(
            client, session_id, f"我买的猫爬架到货散架了，订单号 {order_id}，我想退款。"
        )
        print("\n第二轮：")
        answer, _ = chat(
            client, session_id, "请复述我刚才说的订单号、商品和诉求。", conversation_id
        )
        context_ok = order_id in answer and "猫爬架" in answer and "退款" in answer
        all_ok &= context_ok
        print(f"上下文验证: {'PASS' if context_ok else 'FAIL'}")
        print("自动检查只覆盖关键字段/关键词，请人工复核 expected_solution 的语义和客服语气。")
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        return 0 if verify(args.base_url) else 1
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        print(f"验证失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
