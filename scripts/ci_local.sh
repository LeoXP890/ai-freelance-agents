#!/usr/bin/env bash
# Executa localmente os mesmos passos do .github/workflows/ci.yml (sem GitHub Actions).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/compose/docker-compose.yml" --env-file "$ROOT/infra/env/dry_run.env")
echo "== api: lint, testes, avaliação offline"
(cd "$ROOT/apps/api" && uv sync --frozen --python 3.12 >/dev/null && uv run ruff check src tests migrations \
  && uv run ruff format --check src tests && uv run pytest -q -p no:warnings --junitxml=../../progress/test-reports/api-junit.xml \
  && uv run python -m aicommerce.ai.evals --report ../../progress/test-reports/ai_eval.json >/dev/null)
echo "== web: audit, typecheck, testes, build"
(cd "$ROOT/apps/web" && npm ci --no-fund --silent && npm audit --audit-level=high && npm run typecheck \
  && npx vitest run --reporter=junit --outputFile=../../progress/test-reports/web-junit.xml >/dev/null && npm run build >/dev/null)
echo "== progresso"
python3 "$ROOT/scripts/update_progress.py" --validate
echo "== stack: compose, smoke, carga, backup/restauração, caos do banco"
"${COMPOSE[@]}" up -d --build --wait >/dev/null
(cd "$ROOT/apps/api" && uv run python ../../scripts/smoke_stack.py >/dev/null \
  && uv run python ../../scripts/load_test.py --users 10 --requests 120 --ws-samples 10 >/dev/null \
  && uv run python ../../scripts/backup.py backup >/dev/null && uv run python ../../scripts/backup.py restore-test >/dev/null \
  && uv run python ../../scripts/chaos_db.py >/dev/null)
echo "== observabilidade: regras de alerta (promtool)"
docker run --rm -v "$ROOT/infra/observability:/obs" --entrypoint promtool prom/prometheus:v3.15.0 test rules /obs/rules-tests/aicommerce.test.yml >/dev/null
python3 "$ROOT/scripts/render_alertmanager.py" >/dev/null && docker run --rm -v "$ROOT/infra/env:/cfg:ro" --entrypoint amtool prom/alertmanager:v0.34.1 check-config /cfg/alertmanager.yml >/dev/null
echo "== imagens: scan de vulnerabilidades e segredos (Trivy)"
"$ROOT/scripts/scan_images.sh" >/dev/null
echo "== escritório: regressão visual, acessibilidade e desempenho (Playwright)"
(cd "$ROOT/apps/web" && npx playwright test >/dev/null)
echo "CI local: OK"
