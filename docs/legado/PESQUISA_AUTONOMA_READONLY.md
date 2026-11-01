# Pesquisa autônoma — primeiro incremento seguro (somente leitura)

Data: 25/10/2026. Branch: `feat/autonomous-research-readonly-20261025`.

## Objetivo do proprietário
Quando o AI Commerce estiver configurado, os agentes devem pesquisar oportunidades, concorrentes, fornecedores, condições de entrega, margens e políticas aplicáveis sem exigir do proprietário trabalho de pesquisa manual. O proprietário recebe **propostas de decisão**, com fontes, riscos, custo, requisitos e alternativas; aprova gastos, publicações e contratos mediante MFA. Não confundir "apenas aprovações" com autorização para burlar cadastro/identificação, contratos, pareceres obrigatórios ou regras do canal.

## Entrega deste incremento
- Fonte Mercado Livre Brasil para tendências: somente `GET https://api.mercadolibre.com/trends/MLB`; **exige** token de acesso autorizado, informado em `AIC_RESEARCH_ML_ACCESS_TOKEN` somente na configuração segura do ambiente (não no Git). Sem token, a consulta fica indisponível. Não muta contas, não publica anúncios.
- Seleção opt-in por `AIC_RESEARCH_TREND_SOURCE=mercadolivre` na API; padrão segue `mock`.
- O conector retorna termo, data da coleta, endereço da API de origem e página de pesquisa do termo. **Não** atribui volume de vendas ou crescimento que o endpoint não forneceu.
- Luna utiliza essa fonte quando configurada, mantendo citações e aviso de dados indisponíveis. Falha de HTTP, bloqueio, mudança no contrato ou ausência de resultados => não inventar números nem trocar automaticamente para fixtures.
- Testes unitários isolados de rede. A integração real depende de endpoint acessível e deve ser exercitada em ambiente autorizado com teste de contrato.

## O que ainda NÃO está implementado e impede afirmar autonomia comercial completa
1. **Pesquisa viva de concorrentes:** adaptadores com permissão para recursos oficiais de anúncios e rankings, cache, tempo de validade, normalização por categoria e detecção de anúncios patrocinados; sem scraping que viole políticas.
2. **Descoberta real de fornecedores:** conectores para catálogos públicos/contratados, consulta de preço com data, estoque e lead time, classificação de documentação e verificação separada de evidências. O Nexus atual avalia fornecedores **já cadastrados**, não encontra ou contrata fornecedores automaticamente.
3. **Logística real:** consultar opções permitidas pelo canal/conta, prazos, custo de frete, restrições de postagem e modalidade; não presumir que envio direto pelo fornecedor é permitido.
4. **Compliance vivo:** registro de fontes oficiais com data/versão/escopo, política comercial por vendedor CPF e canal. Informações de fóruns são relatos, jamais homologação jurídica. A Clara produz requisitos e risco, sem liberar gates sozinha.
5. **Cálculo e ranking:** Dante/Finn só ranqueiam margem quando custo fornecedor, tarifas, tributos, frete e devoluções forem conhecidos; apresentar cenários e confiança. Sem preços reais, deixar `UNVERIFIED`.
6. **Orquestração proativa:** Atlas executa pesquisa recorrente com orçamento de IA, cotas de APIs, eventos de mudança, deduplicação e retomada; entrega inbox de propostas e só pede intervenção em ações materiais ou acessos externos.
7. **Credenciais e supervisão:** OAuth e credenciais em secret store, separadas do DRY_RUN comercial; APIs de leitura com permissões mínimas. Autorização inicial de conta continua ato do proprietário quando provedor exigir.
8. **Critérios de aceite:** E2E com fonte oficial de leitura e comprovante, falha/429/rate-limit, fonte desatualizada, produtos indisponíveis, preços sem custo, bloqueios fiscais, e zero efeitos comerciais externos. Avaliação humana das recomendações antes de habilitar IA real.

## Teto, ritmo e trilha (26/10/2026)
- Toda chamada externa (Brave, ML Trends, Melhor Envio sandbox) passa por `ai/research_guard.py`: teto
  `AIC_RESEARCH_BRAVE_MONTHLY_CAP` (Brave é **pago** — US$ 5/1.000 consultas; **0 = desligado**, padrão),
  `AIC_RESEARCH_ML_TRENDS_DAILY_CAP` (300), `AIC_RESEARCH_ME_SANDBOX_DAILY_CAP` (100) e intervalo
  `AIC_RESEARCH_MIN_INTERVAL_S` (1 s). Cada tentativa fica em `research_calls` (OK / ERROR com código HTTP / BLOCKED),
  sem chave nem texto da consulta. Erros contam no teto.
- `GET /v1/research/sources` mostra o estado real de cada provedor (`mock`, `unavailable`, `configured`, `verified`).
  "verified" = houve chamada real bem-sucedida nos últimos 30 dias; não homologa fornecedor, canal, preço ou frete.

## Risco e limites
- A API exige access token desde 2022; pode mudar ou restringir ainda mais o acesso. O conector não contorna isso. Dados de tendência não representam vendas comprovadas.
- Vender com CPF no Mercado Livre não prova autorização para revenda profissional habitual ou venda à ordem. Gates de go-live existentes continuam obrigatórios e em `DENY` sem evidência.
- Não armazenar tokens nas fontes, URLs, prompts ou logs.
- Não instalar provedores pagos nem mudar `AIC_MODE=DRY_RUN` nesta entrega.

## Próxima fatia para implementação
Integrar rankings e anúncios com autorização de leitura apropriada; construir pipeline persistente `market_signal → shortlist → supplier_candidate → landed_cost → compliance_assessment → owner_approval_request`. Somente depois adiciona pesquisas periódicas. Testes antes de conectar contas.
