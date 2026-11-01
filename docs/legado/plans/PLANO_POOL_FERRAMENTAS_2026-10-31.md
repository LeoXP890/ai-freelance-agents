# Plano — pool de ferramentas dos agentes, tendências e automação (31/10/2026)

Objetivo: os agentes trabalharem com dados reais (tendências, Dropi, Mercado Livre) por meio de ferramentas, achando e
publicando produtos lucrativos sozinhos, com o proprietário aprovando só o produto e gastando menos tokens.

## 1. Princípios

1. **Código faz o trabalho pesado, a IA decide e explica.** Buscar, filtrar, casar código de barras, calcular tarifa,
   frete e lucro é feito por ferramenta (código, grátis e exato). A IA só recebe a lista curta já filtrada para
   ranquear e escrever o porquê. É daí que vem a maior economia de tokens.
2. **Leitura livre, escrita só com aprovação.** Ferramenta de leitura roda sozinha. Tudo que publica, muda preço
   ou gasta vira proposta para o proprietário aprovar (política 2026.10.3, homologações e parada de emergência).
3. **Cada ferramenta tem dono, custo e limite.** Registro único com descrição, entradas, se lê ou escreve, custo,
   cache, limite de chamadas e quais agentes podem usar. Toda chamada é auditada.
4. **Cache antes de chamar.** O mesmo dado (tendência do dia, tarifa, frete, ficha no Dropi) é reaproveitado
   dentro da validade, e ninguém paga duas vezes.
5. **Sem burlar bloqueio.** Nada de contornar CAPTCHA ou bloqueio de robô (ex.: Google Trends "explorar" responde
   429). Só fontes abertas, oficiais ou com a sessão do próprio proprietário.

## 2. Arquitetura

- **Registro de ferramentas** (`ai/tools/`): `Tool(name, description, input_schema, effect=READ|PROPOSE, cost_class,
  cache_ttl, rate_limit, agents)`.
- **Executor de ferramentas:** confere permissão do agente (`agent_configs.tools`), cache (`tool_cache` com
  validade), limite de chamadas, orçamento; registra em `tool_calls` (auditoria e custo) e devolve o resultado
  **compacto** (só os campos úteis).
- **Modo agente** (fase 4): a IA escolhe a próxima ferramenta (function calling do Gemini/Claude), com no máximo N
  passos e teto de tokens por execução. Antes disso, as **ferramentas compostas** (pipelines em código) já resolvem
  o caso principal sem IA no meio.
- **Tela "Ferramentas"** no painel do agente: o que ele pode usar, chamadas do mês e custo.

## 3. Pool de ferramentas

### Tendências
| Ferramenta | Fonte | Custo |
|---|---|---|
| `trends.ml_searches` | Mercado Livre `/trends/MLB` e por categoria (busca de quem quer comprar) | grátis |
| `trends.ml_bestsellers` | Mercado Livre `/highlights` (mais vendidos por categoria) | grátis |
| `trends.google_daily` | Feed RSS público do Google Trends Brasil, filtrado para termos de produto; links ignorados | grátis |
| `trends.google_search` | Gemini com busca do Google: "o que está em alta para comprar em X", com fontes | ~US$ 0,003 por busca |
| `trends.seasonal` | Calendário comercial do Brasil (Black Friday, Natal, volta às aulas, Dia das Mães…) | grátis |
| `trends.google_api` | Google Trends API oficial (alfa) — entra quando o Google aprovar a inscrição | a definir |

### Fornecedores
| Ferramenta | O que faz |
|---|---|
| `dropi.search` | Catálogo Dropnacional por termo/categoria: preço, estoque, fornecedor, GTIN, aceita sem NF-e |
| `dropi.product` | Ficha completa: fotos, medidas, peso, variações, prazo de despacho |
| `dropi.freight` | Frete do fornecedor até o CEP |
| `cj.search` / `cj.freight` | Catálogo internacional da CJ (loja própria no futuro), câmbio pela PTAX |
| `suppliers.dossier` | Dossiê dos fornecedores já cadastrados (existente) |

### Mercado Livre (leitura, conta do proprietário)
| Ferramenta | O que faz |
|---|---|
| `ml.match_gtin` | Mesmo produto pelo código de barras: vendedores, preços (mín./mediana/máx.) |
| `ml.fees` | Tarifa oficial por preço e tipo de anúncio |
| `ml.shipping_cost` | Frete por conta do vendedor (Mercado Envios), pelas medidas |
| `ml.category_predict` | Categoria certa pelo título |
| `ml.category_attributes` | Atributos obrigatórios da categoria (evita anúncio recusado) |
| `ml.my_listings` | Meus anúncios: visitas, vendas, conversão |
| `ml.questions` | Perguntas dos compradores (para a Eva responder) |
| `ml.reputation` | Reputação e cor do termômetro (libera Mercado Ads) |

### Cálculo (código puro, zero token)
`calc.profit` (preço → lucro) · `calc.price_for_margin` (preço para a margem alvo) · `calc.max_cpa` (quanto posso
pagar de anúncio por venda) · `calc.fx` (PTAX).

### Conteúdo e conformidade
`listing.title_check` (60 caracteres, termos proibidos) · `listing.regulated_scan` (Inmetro, Anvisa, Mapa) ·
`media.photo_check` (quantidade, tamanho e fundo das fotos).

### Composta (pipeline em código)
`hunt.products`: tendências → Dropi → código de barras no Mercado Livre → tarifa + frete → lucro → descarta o que
não passa (lucro mínimo, estoque, regulado, marca) → **só a lista curta vai para a IA** escrever o porquê.

### Escrita (só por proposta aprovada)
`publish.via_dropi` (robô no painel do Dropi com a sessão do proprietário) · `price.change` · `ads.ml_campaign`
(Mercado Ads, só com reputação verde).

## 4. Por agente: o que melhora e onde economiza

| Agente | Hoje | Com as ferramentas | Economia de tokens |
|---|---|---|---|
| **Luna** (pesquisa) | Pesquisa genérica na web | `hunt.products` + tendências: traz produto real do Dropi com lucro calculado | A IA recebe 10 itens filtrados em vez de páginas de busca (~70–80% menos entrada) |
| **Nexus** (fornecedores) | Busca na web por fornecedor | `dropi.search/product`, `cj.search`: fornecedor já integrado, com estoque | Deixa de pagar busca web para achar o que já está no Dropi |
| **Dante** (comercial) | Margem com dados manuais | `calc.*` + `ml.fees` + `ml.shipping_cost`: preço com dados oficiais | Cálculo sai da IA (zero token) |
| **Finn** (finanças) | Resumo a partir do banco | Pedidos e repasses reais, custo de IA, reserva | Só números agregados no prompt |
| **Orion** (vendas) | Relatório interno | `ml.my_listings`: visitas, conversão, ranking | Métricas pré-agregadas |
| **Logan** (logística) | Frete de teste | `dropi.freight` + rastreio do pedido | Alertas por regra; IA só no caso fora do padrão |
| **Clara** (compliance) | Base de conhecimento + web | `listing.regulated_scan` antes da IA | Só chama a IA se a regra encontrar risco |
| **Vera** (qualidade do anúncio) | Auditoria simulada | `title_check`, `photo_check`, `category_attributes` | Checagens por código; IA só sugere a correção |
| **Iris** (texto) | Texto do anúncio | Atributos reais da ficha do Dropi e do Mercado Livre | Texto com fatos e sem retrabalho |
| **Eva** (atendimento) | Rascunho de resposta | `ml.questions` + pedido + rastreio | Respostas-modelo para as perguntas comuns |
| **Pixel** (anúncios) | Plano de campanha simulado | `ml.reputation`, `calc.max_cpa`, depois `ads.ml_campaign` | Só roda quando a conta puder anunciar |
| **Nova** (canais) | Plano de canais | Dropi (Shopee/TikTok), elegibilidade | — |
| **Atlas** (CEO) | Distribui até 4 passos | Usa `hunt.products` e as rotinas em vez de pesquisa genérica | Menos passos para o mesmo resultado |

Metas: custo médio por produto encontrado abaixo de US$ 0,01, todas as tarefas dentro do teto de US$ 20/mês, e
cache de tendências e fichas com mais de 50% de acerto.

## 5. Fases (eu executo nesta ordem)

| Fase | Entrega | Pronto quando |
|---|---|---|
| **F1 — Fundação** | Registro e executor de ferramentas, cache, auditoria, permissões por agente; `calc.*`; `ml.*` de leitura rodando na VPS | Testes de permissão, cache e limite; ML respondendo na VPS |
| **F2 — Tendências** | `trends.ml_*`, `trends.google_daily` (filtrado), `trends.google_search`, `trends.seasonal` | Lista diária de termos em alta com a fonte |
| **F3 — Dropi + caça** | Sessão do Dropi no servidor, `dropi.search/product/freight`, `hunt.products`, tarefa da Luna e **rotina diária das 7h** | Oportunidades com foto, GTIN conferido e lucro, sem duplicar |
| **F4 — Modo agente** | Function calling com teto de passos e tokens para Luna, Nexus e Dante | Agente escolhe ferramentas sozinho, dentro do orçamento |
| **F5 — Publicação automática** | `publish.via_dropi` (robô na VPS) após aprovação + registro do anúncio | Aprovou → anúncio no ar sem intervenção |
| **F6 — Pós-venda** | Pedidos, rastreio, perguntas (Eva), métricas dos anúncios (Orion), checagens (Vera) | Alertas de atraso e respostas sugeridas |
| **F7 — Mercado Ads** | `ml.reputation` + `ads.ml_campaign` por proposta, com limite diário | Só quando a reputação ficar verde |

## 6. O que o proprietário faz enquanto eu executo

- [ ] **Aprovar os 2 anúncios** em Aprovações (kit de formas R$ 109,90 e arranhador R$ 49,90).
- [ ] **Inscrição na Google Trends API (alfa)** com a sua conta Google: https://developers.google.com/search/apis/trends#apply
  — texto sugerido: "Pequeno e-commerce no Brasil. Usaremos o interesse de busca por região (BR) e categoria
  para escolher produtos para vender no Mercado Livre, com consultas diárias de poucas dezenas de termos e feedback
  direto ao time."
- [ ] **Entrar no Dropi uma vez** na janela ligada à VPS que eu abrir (fase F3). O sistema guarda só a sessão, nunca
  a senha.
- [ ] **Confirmar a reserva de R$ 150** no Cofrinho do Mercado Pago (limites: R$ 60 por pedido, R$ 150 em aberto,
  R$ 300 em 30 dias).
- [ ] **Saldo para pagar o fornecedor:** cada venda gera um pedido no Dropi que precisa ser pago ao fornecedor (Pix ou
  saldo no Mercado Pago). Deixe saldo disponível para as primeiras vendas.
- [ ] **Responder no Mercado Livre** perguntas e mensagens até a Eva estar ligada (F6). A reputação depende disso.
- [ ] **Mercado Ads:** só depois da reputação verde. O Pixel vai propor o orçamento.

## 7. Limites e riscos

- O Google Trends "explorar" bloqueia acesso automático; não será contornado. O feed diário traz mais assunto do que
  produto: serve como sinal filtrado e os links dele nunca são abertos.
- A sessão do Dropi expira; quando expirar, o sistema avisa e pede novo login. As chamadas internas do Dropi podem
  mudar sem aviso, então a ferramenta tem teste de contrato e falha de forma clara.
- Venda como pessoa física com aceite de risco (homologações válidas até 30/11/2026): renovar ou rever antes de
  ampliar o volume.
