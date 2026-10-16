"""Ch04 fail-fast: SiliconFlow rerank, credentials never printed."""

import json
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main():
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    key = (
        values.get("RERANK_API_KEY")
        or values.get("SILICONFLOW_API_KEY")
        or values.get("CHAT_API_KEY")
    )
    url = (values.get("RERANK_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/")
    model = values.get("RERANK_MODEL") or "BAAI/bge-reranker-v2-m3"
    try:
        with httpx.Client(timeout=45) as client:
            response = client.post(
                url + "/rerank",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "query": "MH-W40 的水箱容量是多少？",
                    "documents": ["MH-W20 水箱容量2L", "MH-W40 水箱容量4L"],
                    "top_n": 2,
                },
            )
            response.raise_for_status()
            rows = response.json()["results"]
            passed = (
                len(rows) == 2
                and rows[0]["index"] == 1
                and all(isinstance(r["relevance_score"], (int, float)) for r in rows)
            )
            print(
                json.dumps({"model": model, "passed": passed, "results": rows}, ensure_ascii=False)
            )
            return passed
    except Exception as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(getattr(exc, "response", None), "status_code", None),
                }
            )
        )
        return False


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
