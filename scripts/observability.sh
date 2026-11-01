#!/usr/bin/env bash
# Prometheus + Grafana locais (INF-005): up | down | destroy
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV="$ROOT/infra/env/observability.env"
TOKEN="$ROOT/infra/env/metrics_token"
DC=(docker compose -f "$ROOT/infra/compose/observability.yml" --env-file "$ENV")
case "${1:-up}" in
  up)
    umask 077
    [ -f "$ENV" ] || printf "GRAFANA_ADMIN_PASSWORD=%s\n" "$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" > "$ENV"
    # mesmo token que a API usa: na VPS vem do live.env; localmente do dry_run.env (ou o padrão de simulação)
    APP_ENV="${AIC_ENV_FILE:-}"
    [ -n "$APP_ENV" ] || { [ -f "$ROOT/infra/env/live.env" ] && APP_ENV="$ROOT/infra/env/live.env" || APP_ENV="$ROOT/infra/env/dry_run.env"; }
    tok="$(grep -h '^AIC_METRICS_TOKEN=' "$APP_ENV" 2>/dev/null | cut -d= -f2- || true)"
    AIC_STACK="$(grep -h '^AIC_STACK=' "$APP_ENV" 2>/dev/null | cut -d= -f2- || true)"; export AIC_STACK="${AIC_STACK:-dryrun}"
    [ -n "$tok" ] || tok="$(cd "$ROOT/apps/api" && uv run python -c 'from aicommerce.config import get_settings;print(get_settings().metrics_token)')"
    printf "%s" "$tok" > "$TOKEN"; chmod 644 "$TOKEN"
    mkdir -p "$ROOT/infra/env/secrets"
    [ -f "$ROOT/infra/env/secrets/relay_token" ] || python3 -c 'import secrets;print(secrets.token_urlsafe(32), end="")' > "$ROOT/infra/env/secrets/relay_token"
    [ -f "$ROOT/infra/env/alerting.env" ] || cp "$ROOT/infra/env/alerting.env.example" "$ROOT/infra/env/alerting.env"
    chmod 644 "$ROOT/infra/env/secrets/"* 2>/dev/null || true  # lidos pelos contêineres (usuários não-root)
    python3 "$ROOT/scripts/render_alertmanager.py"
    tokv="$(cat "$ROOT/infra/env/secrets/relay_token")"
    grep -v '^AIC_ALERT_RELAY_TOKEN=' "$ENV" > "$ENV.tmp" || true
    printf "AIC_ALERT_RELAY_TOKEN=%s\n" "$tokv" >> "$ENV.tmp" && mv "$ENV.tmp" "$ENV" && chmod 600 "$ENV"
    "${DC[@]}" up -d
    echo "Prometheus: http://localhost:9090 · Grafana: http://localhost:3001 (admin; senha em infra/env/observability.env)"
    ;;
  reload)  # após editar alerting.env ou criar senhas em infra/env/secrets/
    python3 "$ROOT/scripts/render_alertmanager.py"
    tokv="$(cat "$ROOT/infra/env/secrets/relay_token")"
    grep -v '^AIC_ALERT_RELAY_TOKEN=' "$ENV" > "$ENV.tmp" || true
    printf "AIC_ALERT_RELAY_TOKEN=%s\n" "$tokv" >> "$ENV.tmp" && mv "$ENV.tmp" "$ENV" && chmod 600 "$ENV"
    "${DC[@]}" up -d --force-recreate alertmanager alert-relay prometheus ;;
  down) "${DC[@]}" down ;;
  destroy) "${DC[@]}" down -v ;;
  *) echo "uso: $0 up|reload|down|destroy"; exit 2 ;;
esac
