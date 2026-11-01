# ADR 0001 — Fundação da Fase A

**Data:** 2026-10-23 · **Estado:** aceito · **Contexto:** SDD v2 §6, §8, §13, §15, §23, §26 (Fase A)

## Decisões
1. **Monorepo** `apps/api` (FastAPI, Python 3.12, uv), `apps/web` (Next.js 16 + React Three Fiber),
   `packages/contracts` (JSON Schema gerado da API), `infra/compose`, `infra/env`.
2. **Ambientes isolados por stack compose** (`AIC_STACK` → nome de projeto, volumes e portas distintos).
   `DRY_RUN` é o default; a API **recusa iniciar** em DRY_RUN se encontrar variáveis de credencial live
   (`LIVE_*`, `WOO_CONSUMER_*`, `ML_CLIENT_*`, …). Em LIVE recusa segredos fracos e seed de demonstração.
3. **Identidade local** (argon2id + JWT HS256 + TOTP) atrás de `identity.service`. O SDD cita Supabase Auth;
   como não há projeto/credencial Supabase, o provedor local é o implementado e o adaptador Supabase fica
   como pendência (estado `UNVERIFIED`). Papéis `owner/admin/finance` exigem MFA: sessão sem segundo fator
   recebe **zero** permissões.
4. **Policy Engine puro e versionado** (`policy_versions`, regras JSON + sha256). Mundo fechado (ação
   desconhecida = DENY). LIVE + efeito externo sem `GoLivePolicy=ALLOW` = DENY. Ações materiais =
   REQUIRE_APPROVAL. A mesma função será chamada pelo executor (dupla validação, REQ-DEV-006).
5. **GoLivePolicy** exige todos os gates aplicáveis a canal × origem do fornecedor × fulfillment × UF,
   com `approved_by`, hash de evidência, `policy_version`, validade e escopo. Seed: todos `NOT_STARTED`.
6. **Ledger** append-only (trigger no banco bloqueia UPDATE/DELETE/TRUNCATE), reservas serializadas por
   `SELECT … FOR UPDATE` na política de orçamento, idempotência por chave única, gasto reconciliado consome
   a reserva (sem dupla subtração) e overspend externo é registrado e sinalizado.
7. **Auditoria** append-only com cadeia de hash serializada por advisory lock; `GET /v1/audit/verify`.
8. **Eventos do escritório**: `office_events.seq` (identity) com inserção serializada por advisory lock, de
   modo que ordem de seq = ordem de commit; snapshot e `as_of_seq` lidos numa única instrução SQL.
   Reconexão = snapshot + `after_seq`. WS autentica pela primeira mensagem (token fora da URL).
   Entrega via polling do banco (400 ms) — funciona entre processos (worker → API) sem broker.
9. **Worker** com `FOR UPDATE SKIP LOCKED`; executor determinístico sem LLM nesta fase; tipo não suportado
   falha com `UNSUPPORTED`; lease vencida vira `FAILED` para reconciliação antes de retry.

## Consequências / pendências conhecidas
- Redis está no compose mas ainda não é usado (filas/outbox entram em GOV-004).
- O papel de banco da aplicação hoje é dono das tabelas e poderia desativar triggers; produção deve usar
  papel sem ownership (INF-007). O teste `test_tampering_is_detected` demonstra que a cadeia detecta isso.
- TLS: Caddy local em HTTP; staging/produção usam domínio real com ACME (INF-007/QA-006).
- Cena 3D usa geometria procedural; GLB/glTF do Blender pendente (OFF-001).
