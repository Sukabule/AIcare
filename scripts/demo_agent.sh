#!/usr/bin/env bash
set -euo pipefail
base="${1:-http://127.0.0.1:8000}"
curl --fail-with-body "$base/api/agent" -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","message":"订单 1001 的物流到哪了"}'
curl --fail-with-body -N "$base/api/chat" -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","message":"退货政策是什么"}'
curl --fail-with-body "$base/api/agent" -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","message":"邮费是多少"}'
