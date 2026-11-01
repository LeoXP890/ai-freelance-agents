# AI Commerce — Fase 2: fornecedores, frete e margem (levantamento inicial)

**Pesquisa feita em 25/10/2026.** Operação pretendida: pessoa física (CPF), produtos nacionais, venda via marketplace, prioridade para envio do fornecedor. **Fonte pública não substitui contrato, elegibilidade fiscal/canal nem cotação comercial.** Nenhum fornecedor foi cadastrado como aprovado, nenhuma venda ou despesa foi executada.

## Triagem dos fornecedores

| Candidato | CPF público | Expedição declarada | API / dados | Evidência e decisão de pesquisa |
|---|---|---|---|---|
| DropSystem | Afirma que não exige CNPJ para começar | Fornecedor envia ao comprador | API/estoque/SLA não confirmados | Priorizar avaliação. https://dropsystem.com.br/ |
| DSlite | **Contradição entre páginas oficiais**: há anúncio de plano CPF, mas FAQ de dropshipping exige pessoa jurídica, IE e XML | Envio pelo distribuidor | Página institucional afirma API e XML/XLSX; elegibilidade da API CPF desconhecida | Exigir confirmação da modalidade exata; sem contratar plano. https://app.dslite.com.br/faq/2 e https://app.dslite.com.br/faq/5 |
| Pechincha Livre | Não confirmado | Página oficial declara envio direto e uso de etiqueta | Integração/credenciais não confirmadas | Pesquisa secundária; catálogo focado em calçados, não prova disponibilidade de acessórios de escritório. https://pechinchalivre.com.br/ |
| Dropify | **Não**: FAQ exige CNPJ e IE para revenda | Envio direto declarado | API anunciada; integrador também exige CNPJ | Excluir da shortlist PF (pode reavaliar só se perfil mudar). https://dropify.com.br/perguntas-respostas e https://dropify.com.br/seja-integrador |

**Importante:** os sites acima descrevem sua própria oferta; não auditamos contratos, CNPJ, reputação, estoque, custo unitário, tempo real de despacho ou capacidade de usar etiquetas de conta CPF. A DSlite publicou informação específica de um plano PF, mas há inconsistência com a regra principal. Registrar como CONFLICTING, nunca SUPPORTED.

## Complemento — triagem por perfil e categoria (25/10/2026)

A lista inicial de fontes públicas é **evidência estática de pesquisa**, não um catálogo comercial. A busca `supplier.discover` agora:

- usa o tipo de vendedor lido do banco: no perfil **PF**, exclui da lista elegível para pesquisa provedores declaradamente exclusivos de empresas; no perfil **PJ**, permite investigá-los, sem qualificá-los como aprovados;
- não indica um fornecedor especializado apenas em **calçados** como potencial fornecedor de produtos sem relação com esse segmento;
- rotula plataformas de catálogo genérico com `CATALOG_UNVERIFIED`, e não como correspondência comprovada à categoria;
- mantém `VENDOR_CATEGORY_CLAIM` para correspondência com a categoria declarada pelo próprio fornecedor — ainda sem evidência SKU, contrato, preço, estoque ou postagem;
- preserva fontes, data e perguntas pendentes no dossiê; a disponibilidade comercial continua exigindo verificação independente.

A ausência de candidatos qualificados para uma categoria não deve ser preenchida com fornecedores inventados. Pesquisa web autenticada, se habilitada, continua com resultados marcados não verificados. Dados anteriores não são convertidos automaticamente em oferta real.

## Logística — regras observadas

- O Mercado Livre gera etiquetas para pedidos que usam Mercado Envios; o vendedor precisa preparar o pacote e encaminhá-lo conforme a modalidade habilitada em sua conta. https://vendedores.mercadolivre.com.br/aprender/nota/como-os-envios-do-mercado-livre-funcionam
- Tipos como `drop_off`, `xd_drop_off`, `cross_docking` e `self_service` não são intercambiáveis; validar o `shipping.mode` e `logistic_type` de cada operação. https://developers.mercadolivre.com.br/pt_br/mercado-envios-2
- O Mercado Livre admite, em certas condições, envio **de estoque** diretamente do fornecedor para um centro Full, mas isso **não equivale** a autorização geral de dropshipping pedido-a-pedido. https://vendedores.mercadolivre.com.br/aprender/nota/o-que-considerar-ao-fazer-seu-primeiro-envio-ao-full
- Melhor Envio dispõe de cotação via API com dados de produto/pacote, mas exige autenticação e origem/destino, medidas e peso. https://docs.melhorenvio.com.br/docs/cotacao-de-fretes
- O fornecedor precisa confirmar de forma independente: se aceita etiqueta gerada no marketplace, tempo de postagem, tracking, embalagem sem marca conflitante, NF/documento aplicável, devoluções, indenização, dados de estoque e responsabilidade por descumprimento.
- A cotação de uma transportadora terceirizada não deve substituir automaticamente a tarifa e modalidade de frete contratada na conta Mercado Livre.

## Precificação — não simular lucro como dado real

- O Mercado Livre alterou a estrutura do cálculo de custos em 2026. Usar `listing_type_id`, categoria, preço e parâmetros logísticos aplicáveis, inclusive `logistic_type`, `shipping_mode` e peso faturável conforme contrato. Fonte: https://developers.mercadolivre.com.br/pt_br/descricao-de-produtos/comissao-por-vender
- O simulador oficial pode ser usado para conferir um cenário, mas não valida preço de compra, tributo nem frete do fornecedor: https://vendedores.mercadolivre.com.br/aprender/nota/como-usar-o-simulador-de-custos-do-mercado-livre
- **Margem confirmada** somente quando custo do fornecedor, tarifa por anúncio, frete, devolução, tributo aplicável e custo operacional tiverem evidência atual. Se algum componente necessário não estiver disponível, exibir INDETERMINADA e um cenário **rotulado como estimativa**, nunca como lucro garantido.
- Descontar custos de operação por pedido antes de recomendar margem. Não inventar alíquotas fiscais nem custos de frete.

## Fluxo que os agentes deverão executar

1. Atlas inicia missão periódica em **somente leitura**; escolhe nichos e critérios para comparação.
2. Luna levanta termos, anúncios e concorrentes verificáveis (sem números de vendas inventados).
3. Nexus usa `supplier.discover` e o conjunto de prospects datado, exclui explicitamente PJ-only do perfil CPF; fontes públicas são **leads não verificados**, não SUP-01.
4. Logan compara etiqueta, CEP origem, dimensões, peso, coleta, prazo e tracking com a modalidade do canal, separando cotação real de hipótese.
5. Dante e Finn calculam custo total somente com componentes confirmados; caso contrário apresentam lacunas e cenários.
6. Nova e Clara coletam políticas oficiais atuais do canal/UF e assinalam bloqueios de operação, sem aprovar LEG/TAX ou condições PF desconhecidas.
7. Atlas entrega dossiê ordenado por viabilidade, confiança, preço e risco. O usuário recebe somente opções que exigem sua **decisão**; autenticação em plataformas, contratos e obrigações legais continuam dependendo dos titulares responsáveis.

## Cotação sandbox implementada em código

Há um adaptador isolado `apps/api/src/aicommerce/ai/freight_quotes.py` para **somente cotação** via `POST https://sandbox.melhorenvio.com.br/api/v2/me/shipment/calculate` e testes em `apps/api/tests/test_freight_quotes.py`. Exige credenciais do **sandbox**, `User-Agent` com contato técnico, CEP de origem e destino, peso e medidas confirmados. Como a cotação usa `POST`, o adaptador fixa o único endpoint permitido: não possui métodos de carrinho, compra, pagamento, emissão ou cancelamento de etiqueta. Os preços retornados são marcados `sandbox=True` e `usable_for_commercial_sale=False`.

Ainda falta ligar o adaptador à rotina real do Logan, obter autorização do sandbox e validar o contrato de resposta em ambiente de homologação. **Não representa cotação comercial real** e não serve para precificação LIVE. Fonte oficial: https://docs.melhorenvio.com.br/docs/cotacao-de-fretes e https://docs.melhorenvio.com.br/docs/sandbox.

## Gatilhos para a etapa seguinte

- Habilitar conectores com credenciais escopadas **somente leitura** e teste de contrato: Brave, Mercado Livre Trends/Highlights, custos por vender e Melhor Envio.
- Solicitar diretamente ao provedor (sem compra e com automação autorizada) dados essenciais não públicos: tabela de custos, SKU/estoque, condições CPF, etiqueta e prazo.
- Não converter fonte pública em `Supplier(status=APPROVED)`, não acionar compras, não aprovar homologações, não ativar LIVE.
- Registrar mudanças e conflitos de fontes, validade, versão e referência para revisão.
- Não acrescentar fornecedores à categoria "organização de cabos" sem confirmação do catálogo de produtos correspondente.

## Estado de entrega

**Pronto neste PR:** levantamento público com fontes datadas; lista de candidatos em código, exclusão de PJ-only do shortlist PF, Nexus com contexto das pendências e testes unitários de triagem. **Ainda não pronto:** cotações reais, contratos, teste de etiquetas, consulta de preços autenticada, integração de frete e operação fim-a-fim real.
