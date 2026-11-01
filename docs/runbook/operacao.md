# Runbook de operação — AI Commerce

> Estado: todo o sistema roda em **DRY_RUN**. Nenhuma integração real está homologada; os gates
> LEG/TAX/SUP/CH/OPS estão pendentes e o go-live é negado por política. Este runbook descreve como operar e
> como reagir a incidentes; os passos de produção valem quando houver VPS e gates aprovados (QA-006).

## 1. Mapa rápido
| Componente | Onde | Saúde |
|---|---|---|
| Proxy (Caddy) | `reverse-proxy` :8080 | `curl -I localhost:8080` |
| API (FastAPI) | `api` (papel de banco `aicommerce_app`) | `GET /api/healthz`, `GET /api/readyz` |
| Worker | `worker` (tarefas, execução, fiscal, webhooks, missões, expiração) | métrica `aic_worker_heartbeat_age_seconds`, alerta `WORKER_DOWN` |
| Banco | `postgres` (pgvector) — dono `aicommerce` só para migração | `pg_isready` |
| Migração/seed | `migrate` (one-shot: alembic → dbroles → seed) | log `papel aicommerce_app configurado` |
| Métricas | `GET /api/metrics` com `Authorization: Bearer $AIC_METRICS_TOKEN` | Prometheus |

## 2. Rotina diária
1. `make smoke` (ou `uv run python ../../scripts/smoke_stack.py`) — saúde ponta a ponta.
2. Painel do Finn / `GET /api/v1/alerts` — tratar todos os CRITICAL (ver §4).
3. `uv run python ../../scripts/backup.py backup` e, semanalmente, `restore-test` (relatório em
   `progress/test-reports/restore_check.json`; RPO ≤ 24 h, RTO ≤ 4 h).
4. Custo de IA: `GET /api/v1/ai/usage` — gasto do mês × teto (`AIC_AI_MONTHLY_BUDGET_MICRO_USD`).

## 3. Kill switch (emergência)
- **Acionar:** owner/admin com MFA → `POST /api/v1/emergency-stop {"reason": "..."}` (ou botão na UI).
  Efeito: novas despesas/publicações/aprovações dessas ações bloqueadas; fila de execução congelada exceto
  pausas, compras de fornecedor e reembolsos de pedidos já contratados; delegações revogadas.
- **Pausas externas:** em LIVE, solicitar pausa de campanhas/anúncios nos canais e registrar confirmação ou
  incerteza (em DRY_RUN não há conta real conectada).
- **Liberar:** somente owner → `POST /api/v1/emergency-stop/release`. A política é reavaliada antes de cada
  execução pendente.
- Ao acionar, o sistema **propõe** as pausas: `PAUSE_LISTING` para cada anúncio publicado (executa direto, a política
  permite) e `PAUSE_MEDIA` para cada campanha ativa (vai para aprovação, pois o stop revoga delegações). O proponente
  é `system:emergency_stop`, então quem acionou pode aprovar.
- **Relatório pós-stop** (`GET /api/v1/emergency-stop/report` ou "Ver relatório" na faixa): cada pausa como
  CONFIRMADA (job executado com sucesso), PENDENTE ou FALHOU; jobs congelados; gasto importado após o stop.
  O histórico permanece após a liberação.

## 4. Alertas e resposta
| Alerta | Causa provável | Ação |
|---|---|---|
| `WORKER_DOWN` | worker parado/travado | `docker compose … logs worker`; reiniciar `worker`; jobs `EXECUTING` com lease vencida são reconciliados automaticamente antes de retry |
| `EXECUTION_UNKNOWN` | timeout após envio ao provedor | aguardar reconciliação (consulta por idempotency_key); nunca reenviar manualmente |
| `FISCAL_HOLD` | NCM ausente ou modelo fiscal não aprovado | corrigir NCM; em LIVE depende de TAX-01/TAX-03 |
| `SHIPMENT_LATE` | fornecedor fora do SLA | contatar fornecedor; avaliar suspensão no painel da Nexus |
| `STOCK_STALE` | estoque sem observação > 24 h | atualizar `POST /api/v1/offers/{id}/stock`; anúncios com estoque obsoleto ficam bloqueados |
| `SETTLEMENT_DIVERGENCE` | repasse ≠ esperado | conferir tabela de tarifas do canal (valores atuais são fictícios) e o extrato |
| `ORDER_INCIDENT` | sem estoque (anti-overbooking), compra/reembolso falhou | tratar o pedido; cancelar libera estoque reservado |
| `CAMPAIGN_OVERSPEND` | plataforma gastou acima do teto aprovado | o gasto já ocorreu e está no ledger; pausar (painel do Pixel) e revisar o teto antes de nova campanha |
| `CAMPAIGN_NEAR_CAP` | ≥ 90% do teto consumido | decidir: deixar encerrar ou propor aumento (INCREASE_BUDGET, com aprovação) |
| `CAMPAIGN_SPEND_UNRECONCILED` | métricas importadas ≠ lançamentos SPEND | reimportar o dia; conferir extrato da plataforma |
| `CAMPAIGN_PAUSE_FAILED` | pausa executada com erro | repetir "Propor pausa"; se persistir, pausar direto na plataforma e registrar |
| `CAMPAIGN_ACTIVE_DURING_STOP` / `LISTING_ACTIVE_DURING_STOP` | kill switch ativo e algo ainda não pausado | abrir "Ver relatório" na faixa do stop; aprovar as pausas de mídia pendentes na sala do CEO |
| API `503 DB_UNAVAILABLE` / `readyz` 503 | Postgres fora | `docker compose … ps postgres`; ao voltar, API reconecta e o worker retoma sozinho (backoff até 30 s) — testado em `scripts/chaos_db.py` |

### Prometheus e Grafana (INF-005)

- Local: `./scripts/observability.sh up` → Prometheus `http://localhost:9090`, Grafana `http://localhost:3001`
  (usuário `admin`, senha gerada em `infra/env/observability.env`; painel "AI Commerce — Operação").
- Coleta `api:8000/metrics` com o token de métricas (arquivo `infra/env/metrics_token`, mesmo valor da API).
  Com o banco fora, o `/metrics` continua respondendo e expõe `aic_db_up 0` — o Prometheus não confunde
  "banco fora" com "API fora".
- Regras em `infra/observability/rules/aicommerce.yml` (API/worker/banco fora, 5xx, fila travada, backlog de
  webhooks, alertas críticos abertos, kill switch, orçamento de IA). Testes: `promtool test rules`
  (`infra/observability/rules-tests/`), no CI. Disparo real verificado com `scripts/alerts_e2e.py`.
- Notificações: Alertmanager → e-mail (Gmail) e WhatsApp (API oficial da Meta, via relay). Destinos, roteamento
  e ativação das credenciais em [alertas.md](alertas.md).

### Imagens e vulnerabilidades (INF-007)

- `./scripts/scan_images.sh` (Trivy 0.75.0): falha com HIGH/CRITICAL que já tenha correção ou com segredo.
  Bases fixadas (`python:3.12.14-slim-trixie`, `node:24.21.0-alpine3.24`) + patches do SO no build; a imagem web
  não leva npm/npx no runtime. Vulnerabilidades sem correção publicada ficam no relatório e são reavaliadas a cada
  build. Exceções de segredo exigem justificativa em `infra/security/trivy-secret.yaml`.

## 5. Contas de canal e tokens
- Conta `REAUTH_REQUIRED` (refresh recusado): reconectar a conta (em DRY_RUN: `POST /api/v1/sim/channels/{canal}/connect`).
  Execuções que dependiam dela falham com `TOKEN_EXPIRED` (permanente) e precisam de nova proposta.
- Tokens ficam cifrados (AES-256-GCM com `AIC_PII_KEY`); nunca aparecem em respostas nem logs.

## 6. Segredos e rotação
| Segredo | Variável | Rotação |
|---|---|---|
| JWT | `AIC_JWT_SECRET` | trocar invalida todas as sessões (reautenticação) |
| Dados pessoais/tokens/MFA | `AIC_PII_KEY` | ver "Troca da chave de dados pessoais" abaixo (sem perda de dados) |
| Banco app | `AIC_DB_APP_PASSWORD` | `ALTER ROLE` via `python -m aicommerce.dbroles` com a nova senha; reiniciar api/worker |
| Webhook Woo / ML app id | `AIC_WOO_WEBHOOK_SECRET`, `AIC_ML_APPLICATION_ID` | atualizar no canal e no ambiente juntos |
| Loja Woo (REST) | `AIC_WOO_CONSUMER_KEY/SECRET` | gerar chave nova no Woo, trocar no `live.env`, `deploy.sh`, revogar a antiga no Woo |
| Aplicação ML | `AIC_ML_CLIENT_SECRET` | renovar no DevCenter, trocar no `live.env`, `deploy.sh`; a conta conectada continua valendo |
| Backup | `AIC_BACKUP_PASSPHRASE_FILE` | manter a anterior até expirarem os backups cifrados com ela |
| Métricas | `AIC_METRICS_TOKEN` | atualizar no Prometheus |
Em LIVE a aplicação **recusa iniciar** com qualquer segredo de simulação/desenvolvimento.

### Troca da chave de dados pessoais (`AIC_PII_KEY`)
Cifra dados de clientes, tokens de canal, PKCE e segredos de MFA (TOTP). Troca sem perder nada:
1. Gere a nova: `python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`.
2. No `live.env`: `AIC_PII_PREVIOUS_KEYS=<chave atual>` e `AIC_PII_KEY=<nova>`; `./scripts/deploy.sh`
   (a API lê as duas; grava só com a nova).
3. `docker compose … exec api python -m aicommerce.manage rotate-pii-key` — regrava tudo com a nova (uma transação;
   idempotente) e recalcula o pseudônimo dos clientes. Registro em auditoria `security.pii_key.rotated`.
4. Confira um pedido com dados pessoais e um login com MFA; então apague `AIC_PII_PREVIOUS_KEYS` e `deploy.sh`.
Eventos de auditoria antigos mantêm o pseudônimo anterior (cadeia append-only). Guarde a chave nova fora da VPS.

## 7. Usuários

- **Pelo escritório** (botão "Usuários", exige `users:manage`): criar usuário (senha temporária mostrada
  uma única vez — entregue por canal seguro e peça a troca no 1º acesso), trocar papel, desativar/reativar,
  desbloquear e redefinir MFA. Toda ação é auditada (`user.*`) e mudança de papel/desativação encerra as sessões.
- **Sem escalada:** ninguém concede papel com permissões que não tem, nem altera quem tem mais permissões.
  Na prática, o `admin` técnico gerencia `viewer`, mas `operator` (dados pessoais) e `finance` (documentos
  fiscais) só o `owner`. Ninguém altera o próprio papel nem se desativa; o último `owner` ativo é protegido.
- **CLI** (bootstrap ou emergência): `python -m aicommerce.manage create-user ...`, `reset-mfa --email ...` e `deactivate-user --email ...` (encerra sessões; nunca desativa o último proprietário ativo; auditado).
- **Celular perdido:** o usuário entra com um dos 10 **códigos de recuperação** (uso único) recebidos no cadastro
  do MFA ("Perdi o celular" na tela de verificação) e gera novos em "Usuários → Meus códigos" com o código atual.
  Sem códigos: um gestor usa "Redefinir MFA" e o usuário recadastra o QR no próximo login.
- Troca de senha pelo usuário encerra as demais sessões.

### Documentos fiscais e dados pessoais

- `orders:read` vê o status da NF-e com a chave **mascarada**; o documento completo exige `fiscal:read`
  (owner, finance) e cada acesso grava `fiscal.documents.viewed`. Dados do cliente exigem `orders:pii` (auditado).
- O feed do escritório nunca carrega a chave completa (só os 4 últimos dígitos).

## 8. Deploy e rollback (procedimento alvo para a VPS — QA-006)
1. Backup + `restore-test` verdes.
2. `docker compose … build` → `up -d migrate` (migrações são compatíveis para frente; conferir `alembic downgrade` na
   staging antes) → `up -d api worker web reverse-proxy --wait` → `make smoke`.
3. Rollback: voltar a imagem anterior; se a migração nova não for reversível, restaurar o último backup
   (`restore-test` documenta o tempo real de restauração).

## 9. Avaliação de modelos de IA (antes de trocar modelo/prompt)
`AIC_LLM_PROVIDER=anthropic uv run python -m aicommerce.ai.evals --report …` — exige credencial e custa dinheiro;
comparar `task_pass_rate`, `validator_catch_rate` (meta 100%) e custo com o relatório anterior.

## Custos de IA "HOLD_EXPIRED"
Se o processo do worker cair durante uma chamada ao modelo, não dá para saber se o provedor cobrou. A reserva feita
antes da chamada vence em 10 min e vira um lançamento de custo com `stop_reason = HOLD_EXPIRED` pelo valor MÁXIMO
reservado (modelo "desconhecido"). É conservador de propósito: o teto mensal nunca fica abaixo do gasto real.
Se aparecerem muitos, investigue quedas do worker (logs e alerta de heartbeat).

