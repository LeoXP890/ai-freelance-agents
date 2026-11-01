# ADR 0002 — Aprovações vinculadas e execução idempotente

**Data:** 2026-10-24 · **Estado:** aceito · **Contexto:** SDD §8, §9, §23.1, REQ-DEV-006/008; GOV-002, GOV-004

## Decisões
1. **Vínculo ao conteúdo:** a decisão humana carrega `payload_sha256` + `subject_version` exibidos; divergência
   = `STALE_PAYLOAD`. Nova versão do mesmo assunto expira propostas anteriores não executadas.
2. **Segregação de funções:** quem propõe não decide a própria proposta; decisão exige sessão com MFA.
3. **Atomicidade:** aprovar grava, na mesma transação, decisão (append-only), reserva de orçamento (se monetária),
   `authorization_grants` (uso único, escopo ação+hash+versão+teto, validade) e `execution_jobs`.
   `execution_jobs` é a outbox transacional de side effects — não há tabela de outbox separada.
4. **Dupla validação:** a política é reavaliada na aprovação e de novo pelo executor antes da chamada externa.
5. **Executor:** T1 valida e grava `EXECUTING`+lease → chamada externa fora de transação → T2 registra.
   Timeout = `UNKNOWN`; retry só após `lookup(idempotency_key)` no provedor confirmar ausência do efeito.
   Lease vencida (worker morto) entra na mesma reconciliação. Erros permanentes (REJECTED, TOKEN_EXPIRED,
   UNSUPPORTED, POLICY_DENIED…) não são repetidos; falha libera a reserva.
6. **Kill switch:** congela a fila (exceto pausas) em vez de falhar jobs; ao liberar, a política é reavaliada.
7. **Provedores:** `Mock*` em DRY_RUN com estado externo persistido (`sim_provider_effects`) e falhas injetáveis
   (`_simulate`). Em LIVE não há provedor real: capacidade `UNAVAILABLE` → falha `UNSUPPORTED`.
8. **Sessões:** `users.session_version` no token; troca de senha / redefinição de MFA revoga sessões antigas.

## Pendências
- Ações sem adaptador (SPEND, REFUND, CHANGE_SUPPLIER) falham como UNSUPPORTED se aprovadas.
- Contratos específicos de WooCommerce/Mercado Livre (webhooks, pedidos) entram em COM-004/COM-005.
