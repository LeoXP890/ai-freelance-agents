# Entrega da fase de desenvolvimento — AI Commerce

**Data:** 25/10/2026 · **Modo:** DRY_RUN (simulação) · **Perfil do vendedor:** Pessoa Física (CPF) — ver
[`PERFIL_PESSOA_FISICA.md`](PERFIL_PESSOA_FISICA.md). Produção **não** está homologada; LIVE desligado; nenhuma IA
paga foi usada. O seu aceite visual **não** foi dado por ninguém além de você.

## 1. O que o aplicativo faz hoje
Um aplicativo de computador (macOS e Windows) que é a sede de uma operação de e-commerce tocada por 13 agentes de IA.
Você acompanha tudo num escritório 3D (7 salas, 13 personagens com os estados reais do servidor) e em 17 telas de
negócio. Os agentes pesquisam, avaliam fornecedores, calculam preço e margem, preparam anúncios, conferem regras e
propõem ações. **Nada com efeito externo acontece sem a sua aprovação com MFA**: publicar, gastar, comprar no
fornecedor e reembolsar viram propostas na Central de Aprovações, com dossiê, evidências e o que muda.

## 2. Módulos que funcionam em simulação
Centro de comando · Escritório 3D (e vista 2D) · Aprovações · Agentes · Conversas · Missões · Produtos & pesquisa ·
Fornecedores · Canais de venda (Loja própria, Mercado Livre, Shopee, TikTok Shop, simulados) · Campanhas · Pedidos &
logística · Financeiro · Relatórios · Base de conhecimento · Homologações · Histórico & auditoria · Configurações
(inclui o perfil do vendedor). Mais: kill switch ("Parar novas despesas"), bandeja e notificações, modo offline com
cache, atualização automática assinada, computador lembrado, tema claro/escuro.

## 3. Agentes testados (os 13, provedor simulado)
| Agente | Faz | Testado em simulação |
|---|---|---|
| Atlas | coordena missões (até 6 etapas) e resume | plano, fan-out, resumo, cancelamento, queda do processo |
| Luna | pesquisa oportunidades (fontes simuladas) | citações, cache, erros |
| Nexus | avalia fornecedores | dossiê, riscos |
| Dante | viabilidade e preço por canal | preço só entre os calculados; canal não comprovado para PF declarado como "só simulação" |
| Finn | orçamento, conciliação, margens | números só dos fatos; projeção simulada ≠ obrigação fiscal aprovada |
| Nova | plano de canais | não prepara canal não comprovado fora da simulação |
| Clara | regras e compliance | escala tema fiscal/jurídico e perguntas sobre CPF/PF para validação humana |
| Vera, Iris | auditoria de anúncio e textos | pendência impeditiva, promessas proibidas |
| Eva | atendimento e devoluções | sem promessa fora da política; reembolso por proposta |
| Logan | logística | compra no fornecedor por proposta; SLA |
| Orion | relatórios | números com evidência |
| Pixel | campanhas | orçamento dentro do teto aprovado |

Para **cada tarefa de cada agente** (`tests/test_agents_matrix.py`, 60 cenários): erro temporário → nova tentativa →
concluída (com a falha no ledger); erro temporário persistente → falha após 1+2 tentativas, todas cobradas; resposta
fora do contrato → falha sem resultado; dados insuficientes → falha **sem chamar o modelo**; erro definitivo →
falha na hora. Cancelamento e queda do processo: `test_cancellation.py`, `test_crash_recovery.py`. Avaliação
offline: 17 tarefas a 100% e red-team **19/19** recusados. Auditoria: 13/13 ENGINEERING_READY e MOCK_EVALUATED;
REAL_MODEL_PENDING para todos (qualidade com o Gemini real não avaliada, por regra); PRODUCTION_READY 0.

## 4. Fluxos ponta a ponta executados
- **Jornada completa do proprietário** (`tests/test_e2e_journey.py`, 25 passos, perfil PF): missão → Atlas coordena
  Luna, Nexus, Finn, Clara e Nova → fornecedor simulado aprovado → Dante analisa a viabilidade → anúncio preparado
  (preço/margem em código) → proposta → **sem MFA recusada, com MFA aprovada** → publicação no canal simulado → pedido
  → pagamento capturado → fiscal simulado → compra no fornecedor aprovada → envio → entrega → repasse conciliado →
  **contribuição do pedido igual a receita − tarifa − custo** (R$ 146,26 − 5,84 − 65,80 = R$ 74,62) → histórico da
  proposta na auditoria → cadeia de auditoria íntegra → relatório do Orion.
- Missão DRY_RUN clássica com devolução e reembolso (`test_e2e_dry_run.py`).
- No app (Playwright, 34 cenários): login, missão ao vivo, aprovação com MFA real, queda de rede, base de
  conhecimento, telas de negócio, conversas, tabelas, foto do produto, comparação de propostas, 3D (cena, rótulos,
  teclado, acessibilidade, desempenho nas 4 qualidades), kill switch, WebSocket, poses, rolagem de páginas longas e
  o perfil PF.

## 5. Correções desta fase
Ver [`FINALIZACAO_TECNICA.md`](FINALIZACAO_TECNICA.md) §13 (desenvolvimento) e §14 (Pessoa Física). Destaques:
aprovações identificáveis; "Precisa de você" no agente; nova tentativa em erro temporário do provedor; etiquetas 3D
sem sobreposição; **páginas longas que não rolavam**; Canais que não carregava as homologações ao abrir direto;
perfil Pessoa Física com elegibilidade por canal.

## 6. Testes que passaram (no código final, sem IA paga)
| Suíte | Resultado |
|---|---|
| API (pytest, `CI=true`, rede externa bloqueada) | **515 passaram**, 0 falhas (encerramento; 505 no `f3cf69a`) |
| Avaliação offline / red-team | 17 tarefas 100% · 19/19 recusados |
| Auditoria dos agentes | 13/13 engenharia + simulação; 0 produção |
| Web unitários (vitest) | **80 passaram** |
| E2E no navegador (Playwright, stack Docker real) | **34 passaram** |
| Lint/tipos (ruff, eslint, tsc) | sem erros |
| App nativo macOS (autoteste, Apple GPU) | ver §7 |
| CI do GitHub | ver §7 |

## 7. Commit validado, CI e app nativo
- **Encerramento (versão congelada):** código em **`2c51d39`** — CI `ci` run **40222200740: 4/4 jobs passaram**
  (api, web, progress, compose). O app desktop não mudou desde o `f3cf69a` (sem diferença em `apps/web`/`apps/desktop`);
  o release foi recompilado no `2c51d39` (`.dmg` 3,58 MB, sha256 `abeb42f4517b158d…`, `codesign --verify` ok;
  `cargo test` 3/3). O autoteste nativo de 60 FPS é do `f3cf69a`, com o mesmo código do app. Tag
  `encerramento-desenvolvimento-2026-10-25` no commit que registra estes resultados.
- **Código validado:** commit `f3cf69a` (o commit seguinte só registra estes resultados em documentos e no checklist).
- **CI do GitHub em `f3cf69a`:** `ci` run 40213990083 — **4/4 jobs passaram** (api, web, progress e compose com a stack
  Docker real, smoke, carga, backup/restauração, caos, Trivy e Playwright); `desktop-windows` run 40213990303 —
  **passou** (instalar → abrir → desinstalar num Windows real do GitHub).
- **Falha registrada antes:** o CI do `9ce75c5` falhou num limite de tempo de carga do 3D que eu tinha acrescentado
  (26 s no executor do CI, que renderiza em CPU). Causa, correção e resultado em `FINALIZACAO_TECNICA.md` §12.
- **App nativo macOS (`f3cf69a`, Apple GPU):** escritório 3D a **60,1 FPS** em qualidade Alta (269 chamadas de desenho)
  e **60,1 FPS** em Baixa (182), sem travamento (`evidence/desktop_selftest_f3cf69a.json`). Release assinado para
  atualização: `.dmg` 3,58 MB, `codesign --verify` ok. Não medido: GPU integrada modesta (Windows físico dispensado pelo proprietário em 25/10; sem esse
  hardware aqui).

## 8. Como instalar e abrir
- **Mac:** abra `dist/desktop/0.1.0/AI Commerce_0.1.0_aarch64.dmg` e arraste para Aplicativos (sem certificado Apple,
  o macOS pede "Abrir" e o Keychain pede permissão na 1ª vez). O app que já está instalado e aberto no seu Mac
  **não foi substituído** nesta rodada.
- **Windows:** GitHub → Actions → `desktop-windows` → artefato (SmartScreen: "Mais informações → Executar assim mesmo").
- Ao abrir: servidor `http://localhost:8080/api` (padrão).

## 9. Como executar a simulação
1. `docker compose -f infra/compose/docker-compose.yml --env-file infra/env/dry_run.env up -d` (sobe tudo e migra).
2. Abra o app e entre com `owner@demo.local` / `simulado-123` (cadastre o MFA no 1º acesso).
3. Missões → **Nova missão** (até 6 etapas) e acompanhe no escritório 3D; Canais → **Preparar anúncio** → propor →
   Aprovações → aprovar com MFA; Pedidos → simular pedido → comprar no fornecedor → enviar → entregar; Financeiro.
Tudo aparece como **SIMULAÇÃO**: nada é vendido, gasto, publicado ou emitido.

## 10. Aceite visual — concedido em 25/10/2026
O proprietário aprovou formalmente o produto para a fase DRY_RUN:
[`ACEITE_PROPRIETARIO_2026-10-25.md`](ACEITE_PROPRIETARIO_2026-10-25.md) (alcance e limites). O roteiro
[`VALIDACAO_FINAL_DESKTOP.md`](VALIDACAO_FINAL_DESKTOP.md) segue disponível para conferências futuras.

## 10b. Ajustes de governança do encerramento
Elegibilidade CONDITIONAL com condições verificáveis (UF, modalidade, origem, produto, fornecedor) conferidas na
avaliação e na revalidação antes da execução; mudança material do perfil (UF, modelo fiscal, restrições, tipo) derruba
só as homologações dependentes; aprovações guardam a impressão do perfil e não são reutilizadas se ele mudar.
Testes: `tests/test_governance_conditions.py` (10). Detalhe: ADR 0006 (adendo) e `FINALIZACAO_TECNICA.md` §15.

## 11. O que ficou para a fase de integração e produção
Em [`PENDENCIAS_EXCLUSIVAS_PROPRIETARIO.md`](PENDENCIAS_EXCLUSIVAS_PROPRIETARIO.md) ("PRÓXIMA FASE"): confirmar o que a
conta Pessoa Física pode fazer em cada canal; pareceres do contador (elegibilidade PF, documento fiscal, venda à
ordem); fornecedor real; loja WooCommerce e meio de pagamento que aceite PF; contas de marketplace; VPS e domínio;
IA real (Gemini) quando quiser; certificados do app. **Não estão na lista:** abrir CNPJ ou MEI. Se algum modelo exigir
CNPJ, ele fica bloqueado no app, com o motivo.
