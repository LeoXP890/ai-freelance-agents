#!/usr/bin/env bash
# Cria /opt/aicommerce/infra/env/private.env (0600) na VPS uma única vez: segredos gerados na hora, chaves de IA/CJ
# copiadas dos arquivos privados do Mac. Nada é impresso. Nunca sobrescreve um arquivo existente.
set -euo pipefail
TARGET="${AIC_VPS_TARGET:?defina AIC_VPS_TARGET}"
LOCAL_ENV="${AIC_LOCAL_ENV_DIR:-$HOME/code/AI_Commerce_v2_Kit_Desenvolvimento/infra/env}"
r() { python3 -c "import secrets;print(secrets.token_urlsafe(${1:-32}))"; }
k() { grep -E "^$1=" "$2" | head -1 | cut -d= -f2-; }
{
  echo "# Pilha privada (VPS). Gerado por scripts/private_env_init.sh em $(date +%F). NÃO versionar."
  echo "AIC_STACK=private"
  echo "AIC_ENVIRONMENT=staging"
  echo "AIC_MODE=DRY_RUN"
  echo "AIC_SEED_DEMO_USERS=false"
  echo "AIC_JWT_SECRET=$(r 48)"
  echo "POSTGRES_USER=aicommerce"
  echo "POSTGRES_PASSWORD=$(r 32)"
  echo "POSTGRES_DB=aicommerce"
  echo "AIC_DB_APP_PASSWORD=$(r 32)"
  echo "AIC_PG_PORT=55442"
  echo "AIC_REDIS_PORT=56392"
  echo "AIC_HTTP_PORT=8082"
  echo "AIC_WEB_ORIGIN=http://localhost:8082"
  echo "AIC_PUBLIC_API_URL=http://localhost:8082/api"
  echo "AIC_PUBLIC_BASE_URL=http://localhost:8082/api"
  echo "AIC_PII_KEY=$(python3 -c 'import base64,secrets;print(base64.b64encode(secrets.token_bytes(32)).decode())')"
  echo "# chave de simulação anterior: só para ler dados migrados do Mac; rodar manage rotate-pii-key e depois remover"
  echo "AIC_PII_PREVIOUS_KEYS=c2ltLXBpaS1rZXktZHJ5cnVuLXNvbWVudGUtMzJieSE="
  echo "AIC_WOO_WEBHOOK_SECRET=$(r 32)"
  echo "AIC_METRICS_TOKEN=$(r 32)"
  echo "AIC_LLM_PROVIDER=google"
  echo "AIC_GOOGLE_API_KEY=$(k AIC_GOOGLE_API_KEY "$LOCAL_ENV/ai.env")"
  echo "AIC_AI_MONTHLY_BUDGET_MICRO_USD=20000000"
  echo "AIC_RESEARCH_WEB_SOURCE=gemini"
  echo "AIC_RESEARCH_GEMINI_DAILY_CAP=30"
  echo "AIC_CJ_API_KEY=$(k AIC_CJ_API_KEY "$LOCAL_ENV/cj.env")"
  echo "AIC_FUNDING_OPERATING_SOURCE=mercadopago-saldo-liberado"
  echo "AIC_FUNDING_RESERVE_SOURCE=mercadopago-reserva-piloto"
} | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$TARGET" \
  'set -e; mkdir -p /opt/aicommerce/infra/env; chmod 700 /opt/aicommerce/infra/env; F=/opt/aicommerce/infra/env/private.env;
   if [ -e "$F" ]; then echo "private.env já existe: preservado (nada alterado)"; cat >/dev/null; exit 0; fi;
   umask 077; cat > "$F"; chmod 600 "$F"; echo "private.env criado (0600)"'
