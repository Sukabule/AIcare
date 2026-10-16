.PHONY: dev test lint verify
dev:
	bash scripts/dev.sh
test:
	uv run python scripts/init_db.py --test
	uv run pytest
lint:
	uv run ruff check .
verify:
	uv run python scripts/eval_ch06.py

.PHONY: kb-preview kb-build kb-mine kb-vectorize kb-reset seed-conv eval-retrieval eval-mining
kb-preview:
	uv run python scripts/kb_cli.py preview
kb-build:
	uv run python scripts/kb_cli.py build
kb-mine:
	uv run python scripts/kb_cli.py mine
kb-vectorize:
	uv run python scripts/kb_cli.py vectorize
kb-reset:
	uv run python scripts/kb_cli.py reset
seed-conv:
	uv run python scripts/kb_cli.py seed
eval-retrieval:
	uv run python scripts/kb_cli.py eval-retrieval
eval-mining:
	uv run python scripts/eval_mining.py

.PHONY: eval-rag judge-check
eval-rag:
	uv run python scripts/eval_ch04.py --resume
judge-check:
	uv run python scripts/judge_check.py

.PHONY: eval-intent eval-ch05 bare-agent
eval-intent:
	uv run python scripts/eval_intent.py
eval-ch05:
	uv run python scripts/eval_ch05.py
bare-agent:
	uv run python scripts/bare_agent_loop.py

.PHONY: eval-ch06 eval-coref eval-expand smoke-interrupt
eval-ch06:
	uv run python scripts/eval_ch06.py
eval-coref:
	uv run python scripts/eval_coref.py
eval-expand:
	uv run python scripts/eval_expand.py
smoke-interrupt:
	uv run python scripts/smoke_interrupt.py

.PHONY: eval-ch07
eval-ch07:
	uv run python scripts/eval_ch07.py --limit 1

.PHONY: mcp-up mcp-down mcp-smoke eval-ch08-intent
mcp-up:
	uv run python scripts/mcp_services.py up
mcp-down:
	uv run python scripts/mcp_services.py down
mcp-smoke:
	uv run python scripts/mcp_services.py smoke
eval-ch08-intent:
	uv run python scripts/eval_ch08_intent.py --resume

.PHONY: langfuse-up langfuse-down flywheel cost-report eval-flywheel calibrate-confidence
langfuse-up:
	uv run python scripts/init_langfuse.py
	docker compose -f docker-compose.langfuse.yml up -d --wait --wait-timeout 360
langfuse-down:
	docker compose -f docker-compose.langfuse.yml down
flywheel:
	uv run python scripts/flywheel_pipeline.py --limit 20
cost-report:
	uv run python scripts/cost_by_intent.py
eval-flywheel:
	uv run python scripts/eval_flywheel.py --sample-per-bucket 1
calibrate-confidence:
	uv run python scripts/calibrate_confidence.py


.PHONY: check
check:
	uv run python scripts/check.py
