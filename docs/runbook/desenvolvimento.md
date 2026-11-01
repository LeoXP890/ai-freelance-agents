# Runbook — desenvolvimento local (DRY_RUN)

## Subir tudo
```bash
make up        # postgres, redis, migrate+seed, api, worker, web, reverse-proxy → http://localhost:8080
make smoke     # E2E: health, login, snapshot, tarefa → worker → WS, go-live DENY
```
Usuários de demonstração (somente DRY_RUN, `AIC_SEED_DEMO_USERS=true`): `owner@demo.local` (MFA),
`operator@demo.local`, `viewer@demo.local`; senha em `AIC_DEMO_PASSWORD` (default do seed: ver `seed.py`).
Papéis com MFA (`owner`, `admin`, `finance`) cadastram o app autenticador com QR code no primeiro login;
nenhum segredo TOTP é impresso em log ou terminal.

## Usuários
```bash
cd apps/api
uv run python -m aicommerce.manage create-user --email voce@x.com --name "Nome" --role owner  # senha exibida 1x
uv run python -m aicommerce.manage reset-mfa --email voce@x.com   # celular perdido: novo QR no próximo login
```
5 códigos MFA errados bloqueiam a conta por 15 min (contador separado da senha); código já usado é recusado.

## Desenvolvimento com reload
```bash
make db && make api-dev    # API em :8000
make worker-dev            # outro terminal
make web-dev               # :3000 (NEXT_PUBLIC_API_URL=http://localhost:8000)
```

## Testes
```bash
make test        # pytest (Postgres real, banco efêmero aicommerce_test) + vitest + typecheck
make lint
```
Mudou `office/contract.py`? Rode `make contracts` (há teste de drift no backend e no frontend).

## Kill switch
`POST /api/v1/emergency-stop {"reason": "..."}` (owner/admin com MFA). Liberação: somente `owner`
em `POST /api/v1/emergency-stop/release`. Gastos já ocorridos continuam sendo reconciliados.

## Checklist de progresso (obrigatório ao fim de cada run)
Atualize `progress/tasks.json` com evidências e rode
`python3 scripts/update_progress.py --end-run --summary "..." --tests "..."`.

## Aprovações e execução (DRY_RUN)
- Agentes/operadores submetem `POST /api/v1/proposals`; o owner (com MFA) decide em
  `POST /api/v1/approvals/{id}/approve|reject|request-changes` enviando o `payload_sha256` e a versão exibidos.
- No escritório: botão **Aprovações (N)** ou selo da sala do CEO. Nova e Pixel têm botões de proposta de demonstração.
- O worker executa jobs aprovados nos provedores mock. Para simular falhas inclua no payload
  `"_simulate": "transient_once" | "fail" | "reject" | "token_expired" | "timeout_before" | "timeout_after"`.
- Revogar antes da execução: `POST /api/v1/approvals/{id}/revoke`.
- Trocar senha: botão **Trocar senha** (encerra as outras sessões).

## Catálogo, fornecedores e anúncios (DRY_RUN)
- O seed de demonstração cria 2 fornecedores fictícios (um nacional pronto para aprovação, um internacional em
  prospecção), 2 produtos e ofertas. Tarifas e tributo semeados são FICTÍCIOS (aviso em toda cotação).
- Escritório → **Nexus**: dossiê, checklist SUP-01 e risco; owner aprova o fornecedor. → **Nova**: preparar anúncio
  (preço sugerido para 15% de margem + checagem de conformidade) e propor publicação.
- API: `POST /api/v1/pricing/quote`, `POST /api/v1/channels/{canal}/listings/prepare`,
  `POST /api/v1/listings/{id}/propose-publish`, `POST /api/v1/webhooks/woocommerce`.
- Webhook Woo em DRY_RUN usa o segredo de simulação (`AIC_WOO_WEBHOOK_SECRET`); LIVE exige segredo real.

## Sandbox WooCommerce real (COM-004)

WordPress 7.1.2 + WooCommerce 11.2.1 reais em contêiner, **só local** (127.0.0.1:8090), projeto compose separado.

```bash
./scripts/woo_sandbox.sh up          # cria infra/env/woo_sandbox.env (senhas aleatórias) e instala/ativa o Woo
cd apps/api && uv run python ../../scripts/woo_poc.py   # PoC → progress/test-reports/woo_poc.json
./scripts/woo_sandbox.sh down        # para (mantém dados) · destroy = apaga volumes e credenciais
```

- A PoC publica/atualiza/pausa usando o payload gerado pelo sistema e o `WooCommerceRestProvider.build_request`,
  cria um webhook real (HMAC com o segredo da API) e um pedido no Woo, e confere a ingestão do pedido.
- Autenticação por *application password* (WordPress, ambiente `local`); produção usará chave REST do Woo via
  secret manager e HTTPS. O mu-plugin `infra/woo-sandbox/mu-plugins` só libera entrega de webhook para a rede
  Docker do stack (`reverse-proxy`) e só quando `WP_ENVIRONMENT_TYPE=local`.
- O sandbox entra na rede `aicommerce-dryrun_edge` (só o proxy; sem acesso ao Postgres).
- Fora do CI: exige baixar imagens e o plugin. Sandbox local **não** é homologação de produção (gate CH-WOO).
- Atenção: se outro processo da máquina escutar em `127.0.0.1:8080`, chamadas vindas de contêineres para
  `host.docker.internal:8080` caem nele (por isso a entrega usa a rede Docker).
