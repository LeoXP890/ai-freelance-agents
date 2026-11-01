# AI Commerce — atalhos de desenvolvimento (DRY_RUN). Requer: docker, uv, node 24.
COMPOSE = docker compose -f infra/compose/docker-compose.yml --env-file infra/env/dry_run.env

.PHONY: env up down db api-dev worker-dev web-dev test test-api test-web lint smoke contracts progress
env:            ## cria infra/env/dry_run.env a partir do exemplo
	@test -f infra/env/dry_run.env || cp infra/env/dry_run.env.example infra/env/dry_run.env
up: env         ## stack completo em http://localhost:8080 (API em /api)
	$(COMPOSE) up -d --build --wait
down:
	$(COMPOSE) down
db: env         ## só postgres + redis (desenvolvimento local)
	$(COMPOSE) up -d postgres redis --wait
api-dev: db
	cd apps/api && uv run alembic upgrade head && AIC_SEED_DEMO_USERS=true uv run python -m aicommerce.seed && uv run uvicorn aicommerce.app:app --reload
worker-dev:
	cd apps/api && uv run python -m aicommerce.worker
web-dev:
	cd apps/web && npm run dev
test: test-api test-web
test-api: db
	cd apps/api && uv run pytest --junitxml=../../progress/test-reports/api-junit.xml
test-web:
	cd apps/web && npm run typecheck && npm test
lint:
	cd apps/api && uv run ruff check src tests migrations && uv run ruff format --check src tests
smoke:          ## E2E contra o stack compose
	cd apps/api && uv run python ../../scripts/smoke_stack.py
contracts:      ## regenera packages/contracts a partir da API
	cd apps/api && uv run python ../../scripts/export_contracts.py
progress:
	python3 scripts/update_progress.py --validate
