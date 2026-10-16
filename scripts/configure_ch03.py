"""Set local embedding configuration without printing or embedding credentials in code."""

from pathlib import Path

from dotenv import dotenv_values, set_key

path = Path(__file__).resolve().parents[1] / ".env"
values = dotenv_values(path)
key = values.get("EMBED_API_KEY") or values.get("SILICONFLOW_API_KEY") or values.get("CHAT_API_KEY")
if not key:
    raise SystemExit("Missing SiliconFlow key")
for name, value in {
    "EMBED_BASE_URL": "https://api.siliconflow.cn/v1",
    "EMBED_MODEL": "BAAI/bge-m3",
    "EMBED_API_KEY": key,
    "SILICONFLOW_API_KEY": key,
    "MILVUS_URI": "http://127.0.0.1:19530",
    "MILVUS_COLLECTION": "knowledge",
    "RETRIEVAL_MIN_SCORE": "0.5",
    "RETRIEVAL_TOP_K": "5",
}.items():
    set_key(path, name, value)
print("Ch03 configuration saved. Credentials not displayed.")
