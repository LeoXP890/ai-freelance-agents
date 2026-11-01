# AI Commerce — Fase 3A: consistência comercial no DRY_RUN

**Data:** 25/10/2026. **Ramo de trabalho:** `feat/phase3-dryrun-consistency-20261025`.  
**Rastreamento:** [Issue #4](https://github.com/LeoXP890/ai-commerce/issues/4) e respectivo PR.  
**Regra:** não implica homologação LIVE; as evidências de produção e a Fase 2 continuam independentes.

## Inventário de implementação já existente (antes desta fase)

- Catálogo canônico, variantes e versionamento otimista (`COM-001 = DONE`).
- Preparação de anúncio com cálculo em centavos, compliance, proposta por versão e MFA para execução (COM-003/004/005 em revisão pela homologação externa).
- Mock de WooCommerce e Mercado Livre; contratos Shopee e TikTok Shop ainda não verificados e bloqueados para operação real.
- Campanhas: orçamento/reserva, métricas diárias, reconciliação com ledger e kill switch.
- Pedidos, webhooks, logística, devoluções e conciliação testados na jornada `DRY_RUN`.

## Defeitos corrigidos nesta entrega

### 1. Preparação de anúncio não pode ser usada indefinidamente

A rotina `listings.propose_publish()` só consultava os bloqueios armazenados no momento da preparação. Assim, alterações posteriores em fornecedor, custo ou estoque podiam tornar a proposta obsoleta. A marcação `PRODUCT_CHANGED` era apenas aviso e não bloqueava a proposta.

Agora, **antes da proposta**:

1. Bloqueia `PRODUCT_CHANGED` e exige nova preparação (nova versão + nova aprovação).
2. Reconsulta variante, produto, fornecedor e oferta reais no banco.
3. Reexecuta as regras impeditivas para fornecedor suspenso, produto desativado, oferta vencida e estoque sem observação recente.
4. Compara custo e quantidade de estoque usados na preparação aos valores atuais.
5. Recalcula a cotação e compara tabela de tarifas, perfil tributário, preço e premissas armazenadas.
6. Revalida o vínculo categoria interna→categoria do canal para marketplaces.
7. Versão antiga sem o novo snapshot de estoque não é elegível para propor; requer preparar novamente.

**Execução:** a mesma validação é executada novamente no preflight do executor, após a decisão com MFA e antes da chamada externa. Uma mudança entre autorização e execução produz `POLICY_DENIED` e impede publicar o anúncio obsoleto. Propostas genéricas do simulador permanecem suportadas no `DRY_RUN`; publicação `LIVE` exige anúncio identificado no catálogo.

**Homologação LIVE:** parâmetros fiscais/tarifários ou contratos de marketplace não verificados e categorias sem confirmação oficial impedem publicação real, mesmo com proposta aprovada. Isso não substitui os policy gates nem a homologação externa.

### 2. Idempotência completa de métricas de campanhas

A importação repetida para o mesmo dia comparava somente impressões, cliques e gasto. Agora compara **impressões, cliques, conversões, receita e gasto**. Mesmo dia com valores financeiros diferentes = HTTP 422; valores idênticos = replay seguro, sem novo lançamento financeiro.

## Testes de regressão incluídos

- `test_listings.py`: fornecedor fica sem estoque; estoque altera; custo altera; fornecedor é suspenso; leitura de estoque vence; alteração após MFA é bloqueada no executor; publicação LIVE com taxas não verificadas é recusada. Repreparar cria nova versão e permite proposta.
- `test_catalog_versions.py`: edição marca versão obsoleta e bloqueia a proposta antiga; nova preparação libera proposta.
- `test_ads_killswitch.py`: alteração isolada em qualquer uma das cinco métricas repetidas recebe HTTP 422, sem alterar as somas de receita/gasto/ledger.
- A suíte já possui `test_e2e_journey.py`, `test_e2e_dry_run.py`, `test_orders.py`, `test_pricing.py` e `test_listings.py` para a jornada operacional.

## Verificação

Executar em ambiente de desenvolvimento já preparado conforme o repositório:

```bash
cd apps/api
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q tests/test_listings.py tests/test_catalog_versions.py tests/test_ads_killswitch.py
uv run pytest -q tests/test_e2e_journey.py tests/test_e2e_dry_run.py
```

O CI completo do PR deve prevalecer sobre qualquer resultado local. Registrar URL, SHA e status do workflow; **não marcar tarefas DONE antes dos respectivos critérios e evidências**. Atualizar progresso com `python3 scripts/update_progress.py --end-run --summary "Fase 3A" --tests "CI <link/result>"` quando disponível na árvore de trabalho.

## O que ainda falta para fechar a Fase 3 inteira

- Fase 2: PR de fornecedores ainda depende de revisão/merge e homologação de pesquisa real com dados de fontes autorizadas.
- Staging na VPS, backups/alertas e teste de deploy.
- Loja real, gateway, OAuth Mercado Livre e elegibilidade comprovada para o perfil CPF; acesso de parceiro para Shopee/TikTok se viável.
- Gates legais, fiscais, consumidor, fornecedor e canais exigidos pela política; documentos de origem e validação das margens com frete/tarifas/tributos reais.
- Avaliação dos agentes com modelo real e limites de custo aprovados.

**Até a homologação:** manter `AIC_MODE=DRY_RUN`; nenhuma publicação, compra, gasto publicitário ou venda real autorizada.
