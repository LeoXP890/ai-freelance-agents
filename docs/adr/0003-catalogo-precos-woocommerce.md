# ADR 0003 — Catálogo, fornecedores, preço e WooCommerce (Fase B/C comercial)

**Data:** 2026-10-24 · **Estado:** aceito · **Contexto:** SDD FR-005/006/007/009, §9, §10, §23.3; COM-001..004

## Decisões
1. **Catálogo canônico** (`products`/`variants`) separado das cópias por canal (`channel_listings`), com snapshot
   imutável por versão (`listing_versions`, append-only). Aprovação vincula-se à versão; repreparar expira propostas.
2. **Fornecedor:** avaliação determinística (sem LLM) com checklist espelhando as evidências do SUP-01 e risco 0–100.
   Aprovar fornecedor é decisão interna (exige `suppliers:approve` + MFA + checklist completo) e **não** aprova o gate
   SUP-01 de go-live. Documentos são verificados por humano com permissão.
3. **Preço/margem em código:** inteiros (centavos e bps), custos percentuais arredondados para cima. Tarifas por canal
   (`channel_fee_schedules`) e alíquota (`tax_profiles`) são parâmetros versionados; os valores semeados são
   **fictícios** (`verified=false`, `approved_by_accountant=false`) e geram aviso em toda cotação.
4. **complianceGate** produz BLOCKER/WARNING (fornecedor não aprovado, estoque > 24 h, sem estoque, oferta vencida,
   regulado sem documentos, internacional fora de CROSS_BORDER, margem negativa, contrato do canal inválido).
   Com BLOCKER a publicação não pode ser proposta.
5. **WooCommerce:** mapeamento para o recurso Product v3 validado contra subconjunto do schema público
   (`packages/contracts/providers`). O mock do Woo rejeita payload fora do contrato (VALIDATION). O cliente REST real
   monta a requisição mas não envia (capacidade `UNVERIFIED`) até CH-WOO. Webhook: assinatura HMAC-SHA256/base64,
   dedupe por `X-WC-Webhook-Delivery-ID`, ACK após persistir, processamento no worker com descarte de eventos
   mais antigos que o último aplicado (`date_modified_gmt`). Pedidos ficam `DEFERRED` até COM-009.
6. Mercado Livre/Shopee/TikTok usam payload canônico genérico com aviso `CHANNEL_CONTRACT_PENDING` (COM-005..007).

## Pendências
- Edição de produto/variante com controle de versão; imagens/mídia (gestão de mídia).
- PoC WooCommerce real (instância de teste) para homologar o contrato e o webhook.
