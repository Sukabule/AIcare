#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
  echo '已创建 .env，请填写 CHAT_BASE_URL / CHAT_MODEL / CHAT_API_KEY。'
fi
uv sync --locked
docker compose up -d --wait mysql milvus
uv run --no-sync python scripts/init_db.py
export TIKTOKEN_CACHE_DIR="${TIKTOKEN_CACHE_DIR:-$PWD/.cache/tiktoken}"
uv run --no-sync python scripts/mcp_services.py up
trap 'uv run --no-sync python scripts/mcp_services.py down' EXIT
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8000
