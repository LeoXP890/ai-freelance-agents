# AI COMMERCE
## Software Design Document (SDD) — Versão 2.0
**Data:** 21/10/2026  
**Estado:** especificação de implementação integral em simulação; homologações externas bloqueiam somente operações reais  
**Mercado:** Brasil | **Canais:** WooCommerce, Mercado Livre, Shopee e TikTok Shop | **Infra:** VPS própria

---

# 1. Resumo executivo
O AI Commerce é uma plataforma de gestão comercial multicanal assistida por agentes de IA, com autonomia controlada e escritório virtual 3D. O sistema pesquisa oportunidades, avalia fornecedores, prepara catálogo e campanhas, coordena pedidos e consolida finanças. Gastos, publicação inicial, alterações materiais e obrigações financeiras dependem de autorização humana. Os agentes **não** são o sistema autoritativo: decisões financeiras, políticas e execução externa ficam em serviços determinísticos auditáveis.

**Decisão v2:** implementar TODAS as frentes comercial e visual em paralelo, com contratos, persistência, testes e adaptadores DRY_RUN; produto completo em simulação e integrações reais somente quando homologadas. **Decisão anterior:** começar com monólito modular FastAPI + Next.js, workers persistentes, PostgreSQL e adaptadores. WooCommerce é a escolha provisória para loja própria, condicionada a prova de conceito. Mercado Livre é o primeiro marketplace piloto; Shopee e TikTok Shop só ativam após homologação específica. O escritório 3D é construído em paralelo, mas depende apenas dos eventos reais do backend.

# 2. Escopo, hipóteses e exclusões
**Dentro:** pesquisa e dossiê de produto; fornecedores nacionais/internacionais; catálogo mestre; listagens por canal; preços e custos; workflows multiagente; aprovações; gestão de mídia; importação de pedidos; conciliação; logística; notas via provedor fiscal; conhecimento versionado; escritório 3D; relatórios; observabilidade; testes de homologação.

**Fora da primeira operação REAL (não excluídos do escopo de desenvolvimento simulado): criação de checkout próprio; arbitragem automática comprando em varejistas; automação que contorne CAPTCHA; publicação livre sem aprovação; cálculos fiscais inventados pelo modelo; remessa internacional não homologada; suporte completo de múltiplas empresas (SaaS multi-tenant); animações 3D como fonte de estado.

**Hipóteses não verificadas:** detalhes da VPS, CNPJ e regime fiscal, inscrição estadual, contas dos marketplaces, acessos às APIs, parceiros fiscais, fornecedores contratados, capital de giro e orçamento de mídia. A inexistência dessas informações NÃO impede desenvolvimento com mocks, mas impede go-live comercial.

# 3. Estado da homologação e gates
| Gate | Evidência esperada | Estado 21/10/2026 | Responsável | Bloqueio |
|---|---|---|---|---|
| LEG-01 Empresa e elegibilidade | CNPJ, CNAE, regime, IE e licenças quando aplicáveis | PENDENTE | Proprietário + contador | Toda venda real |
| TAX-01 Venda à ordem nacional | Parecer escrito para fluxo concreto, NF-e e CFOPs | REFERÊNCIA OFICIAL, APLICAÇÃO PENDENTE | Contador | Venda nacional triangular |
| TAX-02 Importação | Parecer de importação e operação por modalidade | PENDENTE | Contador/despachante | Cross-border real |
| TAX-03 Emissão | Credenciamento SEFAZ, certificado/provedor e teste homologação | PENDENTE | Proprietário + fiscal | Emissão em produção |
| SUP-01 Fornecedor | Identificação, contrato, SLA, estoque, documentação e amostra | PENDENTE | Nexus + humano | Oferta do fornecedor |
| CH-WOO | Plugin checkout/pagamento/frete, webhooks e integração fiscal | PENDENTE TESTE | Engenharia | Loja live |
| CH-ML | Conta apta, política de expedição, OAuth e teste autorizado | PENDENTE | Engenharia + proprietário | Mercado Livre live |
| CH-SHO | Resposta oficial da conta sobre fulfillment, tarifas, API ou integrador | PENDENTE | Proprietário | Shopee live |
| CH-TTS | Endereço/depósito BR elegível, política de fulfillment, conta e acesso API | RESTRIÇÕES DOCUMENTADAS, ELEGIBILIDADE PENDENTE | Proprietário | TikTok Shop live |
| OPS-01 Consumidor e LGPD | Termos, privacidade, arrependimento, atendimento, retorno e retenção | PENDENTE | Responsável comercial + jurídico | Go-live |

**Estados do gate:** NOT_STARTED, COLLECTING_EVIDENCE, IN_REVIEW, APPROVED, APPROVED_WITH_RESTRICTIONS, REJECTED, EXPIRED. A aprovação requer `approved_by`, escopo, validade, hash de evidências e versão de política. O `GoLivePolicy` exige todos os gates aplicáveis a canal × produto × fornecedor × modalidade de fulfillment × UF. Qualquer gate crítico ausente produz `DENY`.

**O que foi realmente validado por fonte pública:** Solução de Consulta Cosit 293/2024 (publicada em janeiro de 2025) trata, no caso analisado, dropshipping como venda à ordem e revenda tributada no Anexo I do Simples Nacional; isso não aprova automaticamente cada CFOP/ICMS/UF. Portal NF-e afirma que emissão em homologação não tem valor fiscal e que credenciamento depende da UF. O CDC/Decreto 7.962/2013 exige informações e mecanismos de atendimento e arrependimento. A documentação oficial de APIs comprova disponibilidade da tecnologia, não elegibilidade da conta.

# 4. Requisitos funcionais
| ID | Requisito | Prioridade |
|---|---|---|
| FR-001 | Autenticação com MFA administrativo e RBAC | P0 |
| FR-002 | Definição versionada de agentes (tools, limites, objetivos, saída schema) | P0 |
| FR-003 | Workflow durável com checkpoint e retomada após aprovação | P0 |
| FR-004 | Dossiê de oportunidade com fontes, data e qualidade de evidência | P0 |
| FR-005 | Catálogo mestre e ofertas de múltiplos fornecedores | P0 |
| FR-006 | Dossiê de fornecedor, SLA, estoque, elegibilidade e provas | P0 |
| FR-007 | Margem/contribuição e caixa por canal, com tributos parametrizados | P0 |
| FR-008 | Propostas, aprovação vinculada a payload, auditoria e reservas | P0 |
| FR-009 | Conector WooCommerce com rascunhos e leitura de pedidos | P0 |
| FR-010 | Conector Mercado Livre com OAuth, anúncios e notificações | P1 |
| FR-011 | Conectores Shopee/TikTok Shop por adaptador homologado | P2 |
| FR-012 | Controle de disponibilidade e sincronização anti-overbooking | P1 |
| FR-013 | Tratamento de pagamento, pedido, expedição, entrega e devolução | P1 |
| FR-014 | NF-e por provedor autorizado e políticas fornecidas por contador | P1 |
| FR-015 | Monitoramento e importação de métricas de campanhas | P1 |
| FR-016 | Escritorio 3D com status e cliques vinculados a execuções reais | P0 |
| FR-017 | Cost ledger de IA por agente/tarefa/modelo | P0 |
| FR-018 | Knowledge Hub com busca híbrida, fonte, ACL e validade | P0 |
| FR-019 | Kill switch que bloqueia novas despesas e solicita pausas externas | P0 |
| FR-020 | Certificação por canal e revisão periódica de políticas | P0 |
| FR-021 | Alertas de estoque, atrasos, chargebacks e margens negativas | P1 |
| FR-022 | Modo DRY_RUN e mocks dos provedores externos | P0 |

# 5. Requisitos não funcionais e SLO iniciais
- Rastreabilidade completa das ações que mudam estado externo ou financeiro; logs sem segredos/PII desnecessárias.
- `POST /approvals/*` idempotente com autenticação humana, controle de versão e auditoria.
- Webhook retorna ACK rápido após persistência durável e processa na fila; suporta eventos repetidos e fora de ordem.
- Objetivos iniciais de engenharia (não garantias operacionais): 99,5% disponibilidade API no piloto, p95 leitura de painel < 2 segundos sem consultas pesadas, sincronização de status 3D em até 5 segundos em condições normais, RPO <= 24h e RTO <= 4h inicialmente, a validar após testes.
- TLS obrigatório, segredos em secret manager/variáveis seguras, isolamento de redes, snapshots criptografados fora da VPS, mínimo privilégio.
- Acessibilidade do modo 2D, navegação sem GPU 3D, preferências de movimento reduzido.
- Observabilidade: traces por workflow e request, métricas, alertas, incidentes; evitar dados pessoais em prompts quando possível.

# 6. Arquitetura lógica
```text
Browser 2D/3D (Next.js + React Three Fiber)
    │ HTTPS / WebSocket
Reverse Proxy (TLS / WAF / rate limit)
    │
FastAPI Modular Monolith ── Supabase Auth / RBAC
    ├── Agents + LangGraph checkpoint orchestrator
    ├── Governance / Policy / Approvals / Ledger
    ├── Product Intelligence / Suppliers / Catalog
    ├── Multichannel Commerce Hub / Orders
    ├── Financial Analytics / Fiscal Adapter
    ├── Knowledge Hub / Retrieval / Evidence
    └── Event Stream for 3D
    │
PostgreSQL (+ pgvector) ── Redis + workers ── S3-compatible storage
    │
Adapters: WooCommerce | Mercado Livre | Shopee | TikTok | Ads | Fiscal | Suppliers
```
**Padrão:** monólito modular em produção inicial, workers separados. Processos de side effects usam outbox transacional, job de execução, chave idempotente, deadline, reconciliação antes de retry. Não prometer exatamente uma execução em APIs sem suporte externo.

# 7. Agentes e orquestração
13 papéis lógicos: Atlas (direção), Dante (dropshipping), Luna (pesquisa), Nexus (fornecedores), Iris (criativos), Pixel (mídia), Nova (loja/canais), Finn (finanças), Eva (atendimento), Orion (analytics), Clara (compliance), Logan (logística), Vera (qualidade e conversão).

**Acesso de ferramentas:** o agente envia uma `ActionProposal` tipada; não recebe credenciais financeiras nem ferramentas de alteração externa sem controle. Motor de regras autoriza por canal/categoria/risco; modelos nunca determinam sozinhos resultado jurídico/fiscal. Finn interpreta, mas cálculo fica em código testado. Clara aponta fontes e escalona dúvidas fiscais. Atlas cria plano com limite de fan-out e tokens. A persona no 3D é representação, não processo permanente.

**Orquestração:** estado de tarefa `CREATED→QUEUED→RUNNING→WAITING_EXTERNAL|WAITING_APPROVAL→RESUMED→COMPLETED|FAILED|CANCELLED`. Guardar checkpoint por nó crítico. Workflows longos com timeouts e orçamento de ferramentas. Aprendizados usam registros de experimentos e resultados reais, não memórias inferidas como fatos.

# 8. Governança e aprovações
**Resultado Policy Engine:** ALLOW, REQUIRE_APPROVAL, REQUEST_CHANGES, DENY. Ações que gastem dinheiro, publiquem produto inicial, aumentem orçamento, façam reembolso ou mudem fornecedor significativo exigem aprovação humana no MVP. Pausa de mídia por risco pode ser delegada com autorização explícita.

**Proposta:** `id`, `action_type`, `agent_id`, `subject_version`, `payload_sha256`, `risk_score`, `evidence_refs`, `monetary_scope_minor`, `currency`, `channel_account_id`, `expires_at`, `policy_version`, `status`.

**Estados:** DRAFT→POLICY_CHECK→PENDING_APPROVAL|AUTO_AUTHORIZED|DENIED→AUTHORIZED→QUEUED→EXECUTING→SUCCEEDED|FAILED|UNKNOWN. Acrescentar REJECTED, CHANGES_REQUESTED, REVOKED, EXPIRED. Se payload/versão muda, aprovação expira. Double-submit bloqueado por idempotency key e constraint única. Antes do side effect, executor checa gate comercial, decisão humana, reserva e saldo transacional. Token de autorização de uso único com escopo e validade.

**Orçamento:** ledger imutável (créditos, despesas, reservas, ajustes), moeda em minor units integer. Nunca autorizar campanhas simultâneas só pelo saldo lido fora de transação. `available = approved_cap - reconciled_spend - outstanding_reservations - safety_hold`. Reconciliação assimila gastos externos e reduz reserva correspondente (sem dupla subtração). Campanhas externas podem gastar além de média diária; usar headroom, alertas e pausas preventivas, sem garantia irreal de teto instantâneo.

**Emergency stop:** revogar delegações, travar novas despesas, congelar filas, solicitar pausas nas plataformas, registrar confirmações/incertezas e manter atendimento a pedidos já contratados.

# 9. Commerce Hub e conectores
**Catálogo canônico:** `Product` / `Variant` / `SupplierOffer` / `ChannelListing` / `ChannelPrice` / `FulfillmentPlan`. Cada canal tem estado distinto, mídia e política próprias. Canal recebe produto somente depois de `complianceGate(product, supplier, channel, fulfillment_mode)` e aprovação.

**Interface do adaptador:** `connect`, `validate_account`, `capabilities`, `estimate_fees`, `prepare_listing`, `publish_listing`, `update_listing`, `pause_listing`, `fetch_orders`, `get_order`, `ack_webhook`, `fetch_settlements`, `health_check`. Recursos indisponíveis retornam `UNSUPPORTED` tipado; jamais simular sucesso.

**Matriz de prioridade:** WooCommerce P0 para loja; Mercado Livre P1; Shopee P2 condicionado a política/acesso; TikTok Shop P2 condicionado a fulfillment doméstico elegível e documentação. Fornecedor internacional direto tem gate fiscal/logístico específico, não compartilhado com fornecedor nacional. Nunca usar compra em outro varejista como fulfillment por padrão.

**Pedidos:** `NEW→PAYMENT_CONFIRMED→FISCAL_PENDING→READY_TO_FULFILL→SUPPLIER_ACCEPTED→SHIPPED→DELIVERED` com caminhos `CANCELLED`, `RETURN_REQUESTED`, `REFUNDED`, `INCIDENT`. Os marcos fiscais variam com modelo aprovado; não assumir que NF-e surge sempre após pagamento. Cada evento de webhook é deduplicado; processo aplica transições válidas e reconcilia divergência de status.

# 10. Fiscal e conformidade
`FiscalOperationModel` versionado por UF, canal, modalidade de cumprimento, regime e fornecedor; definido/aprovado por contador. Regras de validação de SKU, NCM, CFOP, CST/CSOSN, cálculo tributário e notas fiscais são serviços determinísticos alimentados com dados homologados. Saída desconhecida = `FISCAL_HOLD`. Provedor fiscal integra emissão em ambiente de homologação; documentos de teste não têm valor fiscal. Em produção, salvar chave, XML, autorização, cancelamento e documentos associados com acesso restrito.

**Módulo de direitos do consumidor:** termos claros, identidade do comerciante, prazo de entrega realista, canal de contato, cancelamentos, arrependimento e retorno. Classificar categorias reguladas e bloquear listagens sem documentação específica. LGPD: minimização, segregação de ambientes, política de retenção, consentimento quando necessário e fluxo de direitos do titular.

# 11. Knowledge Hub e conhecimento dos agentes
**Coleções:** global/policies; dropshipping; fornecedores; marketing; plataformas APIs; legal-fiscal BR; produto e compliance; atendimento; logística; analytics; experimentos; marketplaces (ML/Shopee/TTS).

**Campos de KB:** `document_id`, `source_url`, `publisher`, `document_type`, `jurisdiction`, `channel`, `language`, `published_at`, `verified_at`, `valid_from`, `valid_until`, `owner`, `approval_status`, `access_policy`, `content_hash`, `version`.

**Pipeline:** captura autorizada → validação da origem → limpeza → chunk semântico → embeddings → índice híbrido FTS + pgvector → revisão humana para normas → atualização/expiração. Query router seleciona SQL transacional, regra formal ou retrieval documental. Prompts recebem apenas trechos citados necessários com `source_ids`, nunca documentação inteira. Conteúdo recuperado é dado não confiável, protegido contra prompt injection. Conhecimento legal desatualizado não autoriza operação.

**Especialização:** Atlas resumos executivos; Luna mercados e tendências; Nexus/SUP fornecedores; Dante modelagem comercial; Iris/Pixel ads e políticas; Nova APIs e listagens; Finn regras de custos/ledger; Clara jurídico-fiscal oficial; Logan SLAs; Vera QA; Eva atendimento; Orion experimentos e atribuição.

# 12. Otimização de tokens e custo IA
- Modelo pequeno por default, escalonamento para modelos avançados em casos complexos e alto risco, com roteador testável.
- Prefíxos estáveis e cache conforme suporte do modelo; medir read/write, não presumir economia garantida. A documentação atual de prompt caching distingue gerações de modelos e cobrança de gravação/leitura.
- Batch API em classificação de catálogos, embeddings e avaliações assíncronas; não para decisão interativa urgente. Documentação OpenAI indica redução de 50% em modalidades compatíveis.
- SQL/código primeiro para números, taxas e disponibilidade. Single-source per SKU; cache de pesquisa com TTL por campo; deduplicação por content hash.
- Logs `model`, `input_tokens`, `cached_tokens`, `output_tokens`, `tool_cost_minor`, `task_id`, `request_cost_minor`, `budget_remaining_minor`.
- Limites: tokens por execução, número de chamadas, max agent handoffs, custo por missão, tempo máximo, `stop_reason=LIMIT_REACHED` e aprovação para superar orçamento.
- Relatórios narrativos sobre dados já agregados; resumos incrementais após checkpoints; avaliação offline de acurácia/custo antes de mudar modelo.

# 13. Escritório 3D
Frontend Next.js + React Three Fiber, GLB/glTF exportado de Blender. Uma cena compacta isométrica, agentes selecionáveis, placas de status, salas de direção, pesquisa, marketing, operações, financeiro e reuniões. Interface HTML de painel/chat; modo 2D equivalente. Estado visual vem de `agent_presence` e `workflow_events`, difundidos via WebSocket. Eventos são versionados e contêm `agent_id`, `task_id`, `state`, `timestamp`, `seq`. Reconnect baixa snapshot + eventos posteriores; animação nunca executa ação financeira. Lazy load, LOD, limitadores de FPS e qualidade configurável.

# 14. API e eventos (contratos iniciais)
```http
POST /v1/agent-tasks
GET  /v1/agent-tasks/{id}
GET  /v1/opportunities
POST /v1/opportunities/{id}/proposals
GET  /v1/approvals?status=PENDING_APPROVAL
POST /v1/approvals/{id}/approve
POST /v1/approvals/{id}/reject
POST /v1/approvals/{id}/request-changes
POST /v1/go-live/evaluate
GET  /v1/channels/{id}/capabilities
POST /v1/channels/{id}/listings/prepare
GET  /v1/finance/overview
GET  /v1/agent-presence/snapshot
WS   /v1/office/events
POST /v1/emergency-stop
```
**Eventos:** `agent.task.started`, `agent.task.completed`, `approval.requested`, `approval.decided`, `budget.reserved`, `listing.publish.requested`, `listing.publish.confirmed`, `order.received`, `supplier.stock.stale`, `invoice.authorized`, `integration.unknown_result`, `go_live.revoked`.

**Payload exemplar:**
```json
{
  "proposal_id": "uuid",
  "action": "PUBLISH_LISTING",
  "channel": "MERCADO_LIVRE",
  "product_version": 4,
  "payload_sha256": "...",
  "required_gates": ["LEG-01", "SUP-01", "TAX-01", "CH-ML"],
  "risk": "MEDIUM",
  "monetary_limit_minor": 0,
  "currency": "BRL",
  "idempotency_key": "..."
}
```

# 15. Banco de dados (modelo lógico)
**Identity:** `users`, `roles`, `permissions`, `api_connections`. **Workforce:** `agents`, `agent_configs`, `agent_tasks`, `agent_runs`, `workflow_checkpoints`, `tool_calls`. **Knowledge:** `documents`, `document_versions`, `document_chunks`, `source_evidence`, `retrieval_audits`. **Commerce:** `products`, `variants`, `suppliers`, `supplier_offers`, `inventory_snapshots`, `channels`, `channel_accounts`, `listings`, `listing_versions`, `orders`, `order_items`, `order_events`, `fulfillments`, `returns`. **Finance:** `money_accounts`, `ledger_entries`, `budget_policies`, `budget_reservations`, `settlements`, `fee_quotes`, `ai_usage`. **Governance:** `approval_proposals`, `approval_decisions`, `policy_versions`, `certification_gates`, `gate_evidence`, `audit_events`, `execution_jobs`, `external_action_attempts`. **Office:** `office_scenes`, `avatars`, `agent_presence`, `office_events`.

Chaves estrangeiras explícitas; integrações externas mapeadas por `(provider, account_id, external_id)` únicos. Valores monetários `BIGINT` e moeda ISO; UTC no banco + timezone da apresentação. PII criptografada/ACL. Índices compostos por canal/status/data; tabelas de eventos particionáveis quando necessário.

# 16. Segurança e ameaças
RBAC mínimo, MFA admins, RLS quando aplicável, controles de gastos não contornáveis, segregação por credencial e canal, proibição de credenciais nos prompts, validação de assinatura de webhook, rate limiting, proteção CSRF onde aplicável, logs redigidos, backups testados, dependências e containers escaneados. Prompt injection de fontes externas: nunca executar instruções contidas em sites, emails ou documentos; conteúdo é insumo. Documento sem prova não libera fiscal. Revisão de publicidade para promessas sensíveis e restrições de plataformas.

# 17. Implantação VPS
Container `reverse-proxy`, `web`, `api`, `worker`, `postgres`, `redis`, com storage remoto e backups externos. WooCommerce/WordPress e MariaDB em stack isolada, subdomínios separados, atualização e hardening próprios. A VPS só será dimensionada após CPU/RAM/disco/banda reais; se insuficiente, mover WordPress/DB ou workers para serviço externo. TLS renovado, health checks, logs centralizados, limites CPU/RAM, restauração exercitada, estágio separado de produção. Renderização 3D no navegador.

# 18. Testes e critérios de aceite
Unit: margens, políticas, estados, cálculo monetário, grants. Integração: APIs em sandbox/contas autorizadas, fiscal homologação, webhooks duplicados e fora de ordem. E2E: `research→supplier→dossier→proposal→human approval→draft/publish→verify external` em modo DRY_RUN. Caos: timeout após side effect, falha banco, reinício de worker, token expirado, preço fornecedor alterado, estoque obsoleto, mídia pausada, interrupção de rede WebSocket. Segurança: bypass de aprovação, replay webhook, prompt injection, acesso indevido a documentos fiscais e race condition de orçamento.

**DoD piloto:** zero lançamentos sem aprovação; rastreabilidade de cada alteração externa; rollback/compensação documentada; fluxos de erro confiáveis; resultados de homologação disponíveis; go-live bloqueado por gate faltante. Latências e disponibilidade medidas, não presumidas.

# 19. Roadmap por trilhas paralelas
**Trilha A — Base e negócios:** S1 repo/deploy/auth/ledger; S2 workflows + dossiê; S3 fornecedor + Knowledge Hub; S4 aprovações + WooCommerce; S5 Mercado Livre sandbox + pedidos; S6 piloto sob gates aprovados; S7 Shopee/TTS conforme elegibilidade.

**Trilha B — 3D:** S1 protótipo R3F com assets básicos; S2 status reais via WebSocket; S3 interação/chat/painel de agente; S4 sala do CEO/aprovações; S5 otimização/acessibilidade.

**Trilha C — Compliance:** consulta contábil + documentação societária; homologação 1º fornecedor; confirmação oficial por canal; emissão em homologação; revisão de política de privacidade/CDC; registro das evidências.

# 20. Backlog executável inicial
1. `INF-001` criar monorepo e compose com ambientes; 2. `IAM-001` configurar auth e RBAC; 3. `DB-001` migrações e contratos; 4. `GOV-001` policy engine + auditoria; 5. `FIN-001` ledger + reservas transacionais; 6. `AI-001` registro de agentes + adapter LLM; 7. `AI-002` workflow pesquisa com mocks; 8. `KB-001` ingestão versionada + hybrid search; 9. `COM-001` catálogo mestre; 10. `SUP-001` fornecedor e ofertas; 11. `WOO-001` adapter + webhook; 12. `OFF-001` isometric scene; 13. `OFF-002` event bridge; 14. `CERT-001` checklist evidências/gates; 15. `TEST-001` suíte de falhas/idempotência; 16. `ML-001` OAuth e validação de recursos com usuário de teste.

# 21. Riscos e decisões pendentes
- Acessos/contratos de marketplace não são aprovados por este documento; não afirmar homologação concluída.
- Tratamento fiscal, importação e logística dependem de cenário concreto e profissional habilitado.
- A operação com fornecedor internacional sem estoque próprio pode exigir estrutura não compatível com alguns canais.
- Estabilidade de WooCommerce depende de plugins e hardening; validar PoC antes de comprometimento definitivo.
- Custo de anúncios e capital de giro podem inviabilizar produto mesmo com faturamento crescente.
- Políticas de canais mudam; `policy_version` e revisão obrigatórias.

# 22. Referências de pesquisa (consultadas em 21/10/2026)
1. Receita Federal — Solução de Consulta Cosit 293/2024: https://normas.receita.fazenda.gov.br/sijut2consulta/consulta.action?termoBusca=CONSULTA
2. Portal NF-e FAQ: https://www.nfe.fazenda.gov.br/portal/consulta.aspx/perguntasFrequentes.aspx?AspxAutoDetectCookieSupport=1&tipoConteudo=3Ow1nfTBzIo%3D
3. WooCommerce REST API: https://woocommerce.github.io/woocommerce-rest-api-docs/
4. Mercado Livre Developers: https://developers.mercadolivre.com.br/
5. TikTok Shop Seller Center BR: https://seller-br.tiktok.com/
6. OpenAI Prompt Caching: https://developers.openai.com/api/docs/guides/prompt-caching
7. OpenAI Batch API: https://developers.openai.com/api/docs/guides/batch
8. Decreto 7.962/2013 e CDC (consulta oficial): https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/decreto/d7962.htm
9. LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm
10. LangGraph docs: https://docs.langchain.com/oss/python/langgraph/overview

**Nota de atualização:** tarifas, acesso de APIs e políticas de terceiros requerem rechecagem no momento de homologação e antes de cada go-live. As referências acima não equivalem a aprovações de contas.


---
# 23. Emenda mandatória v2 — Escopo integral em simulação
Este capítulo prevalece sobre qualquer noção anterior de "fora do MVP" ou adiamento de telas. O alvo de engenharia é **AI Commerce v1 integral em DRY_RUN**, incluindo todos os 13 papéis, todas as telas dos módulos comerciais e visuais, workflows completos com erros simulados, catálogo multicanal, marketing, pedidos, logística, atendimento, fiscal simulado e relatório executivo. As políticas fiscais, a publicação real e os provedores que dependem de autorização externa permanecem bloqueados até homologação.

**Definition of Done global:** código executável integrado, migração/persistência real, testes automatizados, validação de permissões, tratamento de erro/timeout, trilha de auditoria, telemetria, documentação de operação e representação no painel e/ou escritório 3D. Não marcar como concluído telas estáticas, mocks sem contrato, classes sem integração ou testes apenas planejados.

**REQ-DEV-001 integralidade:** não deixar funcionalidades como placeholders permanentes. Cada recurso terá estado `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `IN_REVIEW`, `DONE` e critério objetivo de aceite.
**REQ-DEV-002 simulação completa:** `DRY_RUN` para commerce, payments, ads, suppliers, logistics, invoicing e marketplaces; sucesso, falha, timeout, evento duplicado, processamento fora de ordem, token expirado e rejeição.
**REQ-DEV-003 paralelismo:** a trilha comercial e a trilha 3D têm entregas em todos os ciclos; fatos do backend são a fonte de verdade; cena 3D não é meramente decorativa.
**REQ-DEV-004 isolamento:** ambiente simulado separado fisicamente/logicamente do live. Test fixtures exibidos como 'Simulado'. Chaves live não existem em dry-run. Feature flags e autorização por entidade governam ativação.
**REQ-DEV-005 adaptadores:** interfaces tipadas e testes de contrato; `Mock*Provider` sempre implementado; real provider implementado até limite verificável sem credenciais; capacidades `SUPPORTED`, `UNVERIFIED`, `UNAVAILABLE`, `BLOCKED_BY_POLICY`; jamais fingir integração concluída.
**REQ-DEV-006 revisão financeira:** nenhuma despesa ou autorização comercial real liberada por simulação; dupla validação em policy engine e executor, com reservas e idempotência.
**REQ-DEV-007 progresso:** a cada run, atualizar `progress/tasks.json` e regenerar `progress/checklist.html` via script definido na seção 25; nunca registrar DONE sem evidência executável.
**REQ-DEV-008 recuperação:** fluxo deve manter checkpoint, fila, outbox, estado `UNKNOWN` e reconciliação antes de retry.

## 23.1 Contratos para simulação e produção
Interfaces obrigatórias: `CommerceProvider`, `AdsProvider`, `SupplierProvider`, `FulfillmentProvider`, `PaymentReader`, `FiscalDocumentProvider`, `TrendSource`, `KnowledgeRetriever`, `LLMProvider`. Cada comando externo recebe `request_id`, `idempotency_key`, `mode`, `capability`, `approval_token_ref` e `expected_version`; respostas expõem `status`, `provider_ref`, `observed_at`, `error_kind`. Toda mudança externa executa `preflight → policy → authorization → reservation → external action → reconcile → ledger/audit → event`.

Mocks usam seeds determinísticas, relógio controlável, latência/falhas injetáveis e schema compatível com real. A API deve recusar chamadas LIVE se qualquer gate aplicável estiver pendente. `DRY_RUN` não pode acionar API externa com efeitos colaterais.

## 23.2 Experiência 3D completa
React Three Fiber + GLB/glTF com salas CEO, pesquisa, suprimentos, marketing, loja, finanças, atendimento, compliance e reuniões; 13 avatares configuráveis, clique no agente, chat contextual, tarefas, aprovações e indicadores financeiros vinculados ao backend. Há modo 2D equivalente, loading progressivo, redução de movimento, reconexão por snapshot+offset, estados `IDLE/PLANNING/WORKING/TOOL_CALL/WAITING_APPROVAL/BLOCKED/COMPLETED/ERROR`. Não simular resultados como reais. O aplicativo roda no navegador; a VPS hospeda APIs/assets.

## 23.3 Cobertura comercial completa
Produtos: pesquisa com origem e horário; ranking, dossiê e contraindicações; fornecedores nacionais/internacionais, documentação e amostras; catálogo e variantes; preço por canal; feed e sincronização; anúncio e conteúdo; custos e orçamentos; pedidos/pagamento/expedição/devolução; atendimento; faturamento e recebíveis; emissão fiscal simulada com estados e documentos claramente fictícios; conciliação; KPIs. Cada canal tem `ChannelCapabilityMatrix`; WooCommerce PoC prioritária, Mercado Livre segundo, Shopee/TikTok por habilitação específica. Políticas vedam publicação sem evidência.

# 24. Fluxo de trabalho de implementação e provas
## 24.1 Trilhas sincronizadas
Em cada iteração entregar uma vertical comercial e uma representação visual, exemplo: pesquisa ↔ Luna em atividade; fornecedor ↔ painel de Nexus; aprovações ↔ sala CEO; anúncios ↔ marketing; pedidos ↔ operações; financeiro ↔ painel. Agentes e modelos de IA sob demanda, limites por missão, avaliações com fixtures e medições de custo. Não adiar o escritório para depois do comércio.

## 24.2 Gates verificáveis
1. Ambiente e deploy reproduzíveis; 2. Login/RBAC e auditoria; 3. Autorização, ledger e reservas sem concorrência; 4. Missão de pesquisa com fontes; 5. Fornecedor + risco; 6. Dossiê + aprovação; 7. Publicação em mock e verificação; 8. Pedido fictício + devolução + nota fictícia; 9. Campanha e margem; 10. Escritório 3D conectado em tempo real; 11. Falhas/restart/idempotência; 12. Gates live explicitamente NEGADOS antes de homologação. Reproduzir via comandos automatizados documentados, guardar caminhos dos relatórios de teste e hashes/commits.

## 24.3 Critérios de aceite por task
Uma task só passa para DONE com artefato verificado em repositório, teste executado com sucesso e referência no campo `evidence` do JSON, revisão sem blockers e verificação de integração. Se necessário, manter `BLOCKED` com razão e dependência externa. Um log de progresso nunca equivale a permissão de go-live.

# 25. Checklist obrigatório por run (contrato de engenharia)
**Fonte única do andamento: `progress/tasks.json`. Visualização gerada: `progress/checklist.html`. Histórico append-only de runs: `progress/runs.jsonl`.** Não salvar estado de produção apenas no navegador. O HTML é independente (sem CDN), pesquisa e filtra tarefas, mostra percentuais por frente, tarefas bloqueadas, critérios de aceite e evidências; oferece exportação JSON de alterações locais, que precisam ser importadas pelo script para persistir em disco.

Ao FINAL DE TODA EXECUÇÃO do agente de desenvolvimento, inclusive execuções parcialmente concluídas ou bloqueadas:
1. Auditar mudanças efetivas do código e testes executados; comparar com o estado anterior.
2. Atualizar cada item alterado em `progress/tasks.json` com status, notas curtas, paths de evidência e `updated_at`; somente evidência testada permite `DONE`.
3. Executar `python3 scripts/update_progress.py --end-run --summary "..." --tests "..."`; o script valida JSON, regenera HTML atomicamente, registra run e informa progresso, sem promover tarefas automaticamente.
4. Abrir/validar `progress/checklist.html` e conferir totais e tarefas, anexar ao resumo final do run: IDs alterados, testes que passaram/falharam, pendências, custo de IA se disponível e próxima ação.
5. Se houve falha no comando de fechamento, corrigir antes de terminar. Se não houver alterações, registrar run com `no_changes`; se testes falharem, nunca promover DONE.

**Claude Code:** manter `CLAUDE.md` e `AGENTS.md` na raiz com estas obrigações. Configurar hook Stop em `.claude/settings.json` invocando o script de atualização no modo `--hook-stop` como salvaguarda para regenerar HTML/registrar a execução. Hooks não substituem a atualização explícita de status/evidências antes de encerrar. Não interpretar hook como prova de teste ou finalização.

**Compatibilidade:** script utiliza exclusivamente Python stdlib, suporta `--validate`, `--end-run`, `--hook-stop`, `--import <json>`, criação atômica e campos extras sem perda. Atualizar schema, versão e migração quando novas tasks forem adicionadas. `tasks.json` tem `schema_version`, `tasks[]`, `last_generated_at`. IDs são estáveis e não podem ser reutilizados.

## 25.1 Layout inicial recomendado
```text
ai-commerce/
  AGENTS.md
  CLAUDE.md
  .claude/settings.json
  docs/AI_Commerce_SDD_v2.md
  progress/tasks.json
  progress/runs.jsonl
  progress/checklist.html
  scripts/update_progress.py
```

## 25.2 Regra de dependências externas
Homologações `LEG`, `TAX`, `SUP`, `CH`, `OPS` ficam `BLOCKED` enquanto não houver evidências externas. O desenvolvimento correspondente pode estar DONE no modo mock. Bloquear live com decisão negativa auditável e sem exceção por prompt. 

# 26. Roadmap de entrega completa revisado
**Fase A (fundação em ambas as frentes):** infra/Auth/ledger + escritório básico clicável + contrato de eventos.
**Fase B (comércio inteligente):** Atlas/Luna/Nexus/Dante/Finn, KB + catálogo/fornecedores + avatares em tempo real.
**Fase C (governança e plataforma):** aprovação/recusa/revisão, WooCommerce mock/PoC, sala CEO, controles financeiros.
**Fase D (demais operações):** todos os agentes, marketing, pedidos, logística, atendimento, fiscal simulado, marketplace mocks, salas completas.
**Fase E (consolidação):** carga, performance, custo, segurança, falhas, 3D acessível, regressão e documentação operacional. Ao final, FULL_SIMULATION_READY é verdadeiro somente se todos os critérios necessários estiverem DONE e a suíte E2E passar.
**Fase F (go-live por canal):** somente após credenciais, fornecedores, políticas, contador, testes e aprovação humana. `LIVE_READY` é por conta/canal/produto/fornecedor, não flag global.

# 27. Fontes, premissas e não-alegações
Preservam-se as referências da seção 22. O presente documento define especificação e plano: nenhum componente foi implementado neste ato além dos artefatos de acompanhamento entregues. Os percentuais do checklist começam em zero; nenhuma homologação externa foi concluída nesta atualização. Priorização pode ser revista por ADR versionada, sem excluir funcionalidades silenciosamente.
