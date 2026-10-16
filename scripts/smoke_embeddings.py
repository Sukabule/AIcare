"""Ch03 fail-fast gate: real SiliconFlow BGE-M3 embeddings, no fallback."""

import json
import math
from pathlib import Path

from dotenv import dotenv_values
from langchain_openai import OpenAIEmbeddings


def main() -> int:
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    key = (
        values.get("EMBED_API_KEY")
        or values.get("SILICONFLOW_API_KEY")
        or values.get("CHAT_API_KEY")
    )
    if not key:
        print("Missing SiliconFlow API key in .env")
        return 1
    model = OpenAIEmbeddings(
        model="BAAI/bge-m3",
        base_url="https://api.siliconflow.cn/v1",
        api_key=key,
        check_embedding_ctx_length=False,
        max_retries=0,
        request_timeout=45,
    )
    try:
        vectors = model.embed_documents(["邮费是多少", "运费怎么算"])
        valid = len(vectors) == 2 and all(
            len(vector) == 1024 and all(math.isfinite(x) for x in vector) for vector in vectors
        )
        print(
            json.dumps(
                {"model": "BAAI/bge-m3", "dimensions": [len(v) for v in vectors], "passed": valid}
            )
        )
        return 0 if valid else 1
    except Exception as exc:
        # Do not print credentials or upstream response bodies.
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
