# ADR 0008 — Pagamento ao fornecedor com saldo liberado e reserva

Data: 30/10/2026. Decisão de produto aprovada pelo proprietário nesta conversa. **Integração de débito real ainda não implementada/homologada por esta decisão.**

## Decisão

O AI Commerce deverá automatizar o pagamento separado ao fornecedor: priorizar dinheiro efetivamente disponível da operação e usar um fundo de reserva para a insuficiência temporária. Isso não é divisão automática do checkout do Mercado Livre. Não se assume acesso ao dinheiro que o marketplace ainda retém.

O fundo terá saldo depositado e autorização de uso com limites explícitos. O valor ainda não foi definido. Até configurar fonte, limites e autorização executável, a capacidade de débito permanece desabilitada. Não solicitar novamente aprovação conceitual deste fluxo; ainda será necessário delimitar o piloto e o orçamento real.

## Sequência determinística a implementar

1. Validar pedido pago, fornecedor/beneficiário aprovado, cotação vigente com moeda/custos completos, produto, logística, gates aplicáveis e autorização de execução. Compra cancelada/disputada não segue automaticamente.
2. Consultar saldo financeiro real e reconciliado; excluir recebíveis retidos, valores já comprometidos, devoluções e outros bloqueios. Um evento de pagamento aprovado ou extrato importado não basta para declarar dinheiro transferível.
3. Reservar atomicamente a fonte principal e eventual complemento da reserva. Exemplo hipotético: obrigação de R$ 80, saldo livre de R$ 30 → R$ 30 da operação + R$ 50 da reserva. Se não houver saldo/limite para o total, não fazer pagamento parcial ao fornecedor; gerar pendência.
4. Executar uma única obrigação ao fornecedor com chave idempotente vinculada a pedido, fornecedor e versão da cotação. A composição interna de fontes não implica que o fornecedor aceite dois pagamentos; consolidar na conta/carteira apropriada somente com provedor autorizado.
5. Confirmar o resultado por referência externa e conciliação. Timeout após tentativa → UNKNOWN, mantendo recursos comprometidos e exigindo consulta, sem repetir o débito às cegas.
6. Registrar quanto veio de cada fonte e a exposição da reserva por pedido. Quando o recebimento se tornar disponível, abater contabilmente a exposição coberta por ele, sem pagar o fornecedor novamente. Transferência bancária para recompor o fundo depende da configuração do provedor e política aprovada, não de simples mudança contábil.

## Controles necessários

- Saldo disponível, orçamento autorizado e reserva financeira são conceitos distintos. **`finance/ledger.py` hoje calcula disponibilidade de orçamento; não é prova de caixa bancário ou saldo liberado no Mercado Pago.**
- `commerce/orders.py` e o executor já vinculam SUPPLIER_PURCHASE a autorização/reserva/gasto, mas não constituem integração real de duas fontes de caixa.
- Limites por pedido, período e exposição total da reserva; piso protegido; fornecedores e moedas autorizados; alerta de insuficiência. Valores ainda não configurados, sem teto financeiro inventado.
- Leituras recentes e reconciliação; locks e idempotência no servidor; deduplicação de eventos; referência de versão para não consumir duas vezes o mesmo saldo. LLM não autoriza nem calcula o débito final.
- Uma carteira do fornecedor pode exigir recarga prévia. Recarga e compra são operações diferentes; registrar sem contabilizar a mesma saída duas vezes. Não presumir saque livre do saldo.
- Para China: cotação de câmbio válida, custos cambiais, frete e impostos aplicáveis antes de reservar; nunca comparar ou somar BRL/USD sem conversão documentada.
- MFA/delegação e gates do SDD permanecem. Aprovação de arquitetura não é autorização ilimitada para gastos nem habilitação de LIVE.

## Critérios de validação antes de liberar

Saldo principal suficiente; saldo parcial + reserva; saldo retido ignorado; reserva insuficiente; concorrência; evento duplicado; cotação vencida; mudança de beneficiário; timeout com efeito incerto; cancelamento/chargeback; recebimento posterior sem segundo pagamento; falha na recomposição; moeda/câmbio divergentes. Provar em DRY_RUN e depois no provedor homologado, dentro do escopo autorizado.

## Pesquisa que sustenta a distinção

- [Recebimento no Mercado Livre](https://vendedores.mercadolivre.com.br/nota/como-recebo-o-dinheiro-das-minhas-vendas-no-mercado-livre): o uso do dinheiro pressupõe disponibilidade na conta.
- [Split do Mercado Pago](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/how-tos/integrate-marketplace?scope=prod): integração de checkout para marketplace próprio; não comprova repasse automático ao fornecedor de uma venda na conta ML.
- [CJ: pagamento automático via saldo](https://cjdropshipping.com/help-center?keyWord=pay&navType=search&searchType=content): automatização usa carteira CJ; não torna o recebível ML disponível.

Nenhum dinheiro foi movido, conta criada, limite alterado ou pagamento integrado nesta rodada documental. A implementação e testes específicos de duas fontes ainda estão pendentes.

## Implementação em DRY_RUN — 31/10/2026

Domínio implementado em `apps/api/src/aicommerce/finance/funding.py`, migração `0025_funding_cash_sources.py`, testes em `apps/api/tests/test_funding.py`. **Somente DRY_RUN:** fora dele alocar e pagar levantam `FUNDING_DISABLED`. Não há conector de saldo real, débito, transferência ou recomposição.

- **Fontes (`cash_sources`)**: OPERATING (saldo liberado) e RESERVE (fundo). Sem política aprovada não financiam nada. Operação exige piso; reserva exige piso, limite por pedido, limite por período (valor + dias) e exposição máxima. Nenhum valor padrão é presumido.
- **Leituras (`cash_balance_snapshots`, append-only)**: `available` liberado/transferível e `retained` apenas informativo, nunca alocado. Origem simulada só em fonte simulada e vice-versa; leitura no futuro recusada; leitura mais velha que `AIC_FUNDING_BALANCE_MAX_AGE_S` (padrão 900 s) bloqueia.
- **Alocação**: trava as fontes (`FOR UPDATE`, ordem fixa), valida kill switch, moeda (sem conversão), cotação vigente, pedido `CAPTURED`/`READY_TO_FULFILL` e fornecedor `APPROVED`; usa a operação até o piso e completa com a reserva dentro dos limites. Se faltar → `BLOCKED` com motivo, sem pernas e sem pagamento parcial. Índice único parcial impede duas alocações vivas para a mesma obrigação (pedido + fornecedor + versão da cotação).
- **Comprometido**: alocações RESERVED/PAYMENT_PENDING/PAYMENT_UNKNOWN, mais PAID confirmadas depois da leitura usada (assim o débito não é subtraído duas vezes quando uma leitura nova já o reflete).
- **Pagamento único**: `start_payment` abre uma intenção de valor total com chave estável, conferindo beneficiário, cotação e pedido. Timeout → `PAYMENT_UNKNOWN`, dinheiro continua comprometido e nova tentativa é recusada até a consulta. `FAILED` só com confirmação de que não houve débito. Eventos duplicados não mudam estado; resultado fora de ordem não desfaz `PAID`; sucesso após `FAILED` vira `RECONCILIATION_CONFLICT` auditado para revisão humana.
- **Cancelamento/chargeback**: liberação só antes do pagamento; com pagamento em curso, incerto ou feito, a liberação é recusada e a exposição permanece.
- **Recebível posterior**: abate contabilmente a exposição da reserva da alocação paga (limitado ao que a reserva cobriu), idempotente, sem criar pagamento ou alocação.

Testes: 27 casos cobrindo todos os critérios de validação acima em PostgreSQL real, incluindo corrida de 8 alocações concorrentes e de 6 réplicas da mesma obrigação. Checagem de mutação manual: contar retido como disponível, não reter `PAYMENT_UNKNOWN` ou remover o `FOR UPDATE` faz os testes correspondentes falharem.

**Executor (31/10, segunda etapa):** com `AIC_FUNDING_OPERATING_SOURCE` (e opcionalmente `AIC_FUNDING_RESERVE_SOURCE`) configurados, o executor de `SUPPLIER_PURCHASE` cobre o total com caixa e abre a intenção única na mesma transação que marca EXECUTING, antes de chamar o fornecedor; sem caixa, o job falha com o motivo, o pedido vai a INCIDENT e o fornecedor não é chamado. Resultado SUCCEEDED/FAILED/UNKNOWN e a reconciliação (efeito encontrado/ausente) são aplicados à alocação. Obrigação = pedido + fornecedor: outra proposta para o mesmo par é recusada enquanto houver alocação viva ou paga. Pedidos já vendidos continuam atendidos sob emergency stop (mesma regra do executor/orçamento). Sem as variáveis, o fluxo anterior só com orçamento continua igual. Beneficiário ainda é a versão do cadastro do fornecedor (`supplier:<id>:v<versão>`), não um PIX/carteira real. Testes: `apps/api/tests/test_funding_executor.py` (8 casos no fluxo real de pedido).

**Ainda pendente:** tela financeira; importador de extrato/consulta de saldo real; provedor de pagamento por fornecedor; recomposição real do fundo; valores de piso/limites e origem do dinheiro definidos pelo proprietário com cenários; conversão cambial documentada para China.
