# Finalização técnica — AI Commerce

**Data:** 25/10/2026 · **Ponto de partida:** `2b55a0e` (tag `baseline-finalizacao-2026-10-25`, igual ao `origin/main`
verificado antes de começar) · **Versão entregue:** tag `finalizacao-tecnica-2026-10-25.2` (commit indicado pela tag;
o resultado do CI desse commit está no fim deste documento).

O sistema **não** foi reconstruído: a arquitetura existente foi mantida e só defeitos reproduzidos foram corrigidos,
cada um com teste de regressão. Nada comercial real foi executado e **nenhuma IA paga foi consumida** (todos os
testes usam o provedor simulado). Produção **não** está homologada.

---

## 1. Arquitetura mantida
FastAPI + PostgreSQL (pgvector) + Redis · orquestrador próprio de missões/tarefas com checkpoints · governança
determinística (políticas, propostas, aprovação com MFA, reservas e ledger, kill switch, gates de go-live) ·
adaptadores por canal com estados de homologação · Tauri 2 + Next.js (exportação estática) + React Three Fiber ·
autenticação/MFA/RBAC/sessão lembrada no cofre do sistema. Nenhuma migração de tecnologia (sem LangGraph, Electron,
Vite). Migrações de banco apenas **aditivas** (0016–0020 nesta etapa).

## 2. Funcionalidades existentes (resumo)
Catálogo, fornecedores (dossiê SUP-01), preço e margem por canal, anúncios (Woo, ML, Shopee, TikTok — mock +
clientes reais conforme homologação), pedidos e pagamentos simulados, logística, devoluções/reembolsos, repasses e
conciliação, fiscal simulado, campanhas, 13 agentes com tarefas estruturadas, missões do Atlas, base de
conhecimento, auditoria encadeada, app desktop (17 telas, temas claro/escuro, bandeja, notificações, sessão
lembrada, atualização automática), escritório 3D com 7 salas e 13 personagens, kit de implantação na VPS.

## 3. Correções desta finalização (todas com teste)
| Área | Defeito reproduzido | Correção | Prova |
|---|---|---|---|
| Custo de IA | Limite por execução planejava com estimativa (bytes÷3); pior caso podia passar do saldo | Planejamento e reserva pelo **limite superior** (bytes de sistema+usuário+schema+margem) e raciocínio do Gemini | `test_ai_budget_adversarial.py` (60 chamadas aleatórias, fronteira exata) |
| Custo de IA | Falha **depois** da resposta (corte por limite, recusa, JSON inválido) liberava a reserva **sem lançar custo** | Lança o uso real informado pelo provedor; timeout após envio/erro inesperado lança o pior caso | `test_llm_failure_accounting.py` |
| Proteção de créditos | Nada impedia IA paga em dev/CI | Configuração recusa provedor pago em local/teste sem `AIC_ALLOW_PAID_AI=true` e **sempre** em CI; testes bloqueiam rede externa | `test_paid_ai_guard.py` |
| Orquestração | — (verificado) | Cancelamento durante a resposta do modelo não grava nada, não cria etapas, mantém o custo | `test_cancellation.py`, `test_crash_recovery.py` (queda real em 8 fronteiras) |
| Agentes | Finn/Orion/Logan/resumo do Atlas aceitavam **números inventados** citando fatos reais; Nexus aceitava fornecedor de risco ALTO sem riscos; Vera podia omitir pendência impeditiva | Validadores conferem números contra os fatos citados, risco do dossiê e pendências | `evals` red-team 16/16; `test_agent_audit.py` |
| Agentes | Entrada ausente derrubava a tarefa com `KeyError` | Erro tipado de entrada | evals (13 agentes) |
| Pedidos | Pedido **pago e cancelado** não devolvia o dinheiro | Cancelamento interno cria proposta de reembolso (aprovação); cancelado pelo canal gera alerta crítico (sem estorno em dobro) | `test_order_cancel_refund.py` |
| Financeiro | Legenda da contribuição omitia reembolsos; "margem" ambígua; contribuição podia ser lida como lucro | Composição explícita (bruta − reembolsos − tarifas = líquida − custo = contribuição), "Margem de contribuição", "não é lucro nem caixa" | `financeTerms.test.ts` |
| Auditoria (tela) | Filtro "Missões" quase nunca achava eventos (missão é `agent_task.*`) | Filtro por vários prefixos reais | `test_missions.py::test_audit_filters_and_pagination` |
| Tema claro | Códigos ilegíveis (estilo global antigo); painel de usuários com botões/seletores ilegíveis | Tokens do tema | capturas `screenshots/final/light-*` |
| 3D | "Bloqueado" e "erro" quase iguais; "concluído" = parado; "aguardando" igual a parado com movimento reduzido | Poses distintas para os 7 estados, inclusive sem animação | `poses.test.ts` + E2E A19 (rotação real dos membros, 7 estados, 2 modos) |
| Implantação | Monitoramento preso à rede da simulação (não subiria na VPS) | Rede/token da stack configurada | `docker compose config` com `AIC_STACK=live` |
| Implantação | Sem volta rápida após atualização ruim | `deploy.sh --rollback` (última versão saudável) | `bash -n`; procedimento no guia |
| Missões | "0 s" de duração parecia falha | "< 1 s" | `workflow.test.ts` |

Correções anteriores da auditoria (A01–A21) continuam com regressão na suíte — ver §8.

## 4. Testes no código final
| Suíte | Resultado |
|---|---|
| API (pytest, Postgres real) | **428 passaram**, 0 falhas (ruff ok) |
| Avaliação offline dos agentes (mock) | 17 casos de tarefa (13 agentes) 100% · red-team 16/16 recusados |
| Auditoria dos agentes | 13/13 ENGINEERING_READY · 13/13 MOCK_EVALUATED · 13/13 REAL_MODEL_PENDING · 0 PRODUCTION_READY |
| Recuperação da base de conhecimento (gabarito) | mesmas palavras: recall@5 1,00 (MRR 0,958) · paráfrase: recall@5 0,417 (exige embedder semântico ≥ 0,75 para ligar) |
| Interface (vitest + tsc) | **70 passaram** |
| Navegador (Playwright contra a stack) | **32 passaram** (+ capturas sob demanda em `e2e/capture/`) |
| Rust (cargo test) | 3 passaram |
| Desktop nativo macOS (autotestes no WKWebView) | escritório 60,1 FPS alta / 60 FPS baixa; atualização 0.1.0→0.1.1 e pacote adulterado recusado; janela/bandeja; sessão lembrada |
| Windows (GitHub Actions, windows-2025) | instalar → abrir (WebView2) → desinstalar: ok (run 40158034240, base anterior; repetido no CI deste commit — §9) |

## 5. Builds
- macOS release: `AI Commerce.app` 6,72 MB, `.dmg` 3,41 MB (SHA-256 do dmg começa com `ff5c4384c52661cc`), assinatura
  ad-hoc conferida (`codesign --verify --deep --strict`), sem código de autoteste no bundle. Instalado em /Applications.
- Windows: NSIS `.exe` 3,0 MB e `.msi` 4,1 MB (artefato `desktop-windows` do Actions), sem assinatura de código.
- Atualização automática: pacotes assinados com a chave do updater (`scripts/build_desktop_release.sh`).

## 6. Desempenho (medido — sem números inventados)
| Onde | Qualidade | FPS | Pior quadro | Draw calls | Triângulos | Memória |
|---|---|---|---|---|---|---|
| Mac do proprietário (GPU Apple, WKWebView) | Alta | 60,1 | 21 ms | 269 | 233 mil | app 66 MB · web 63 MB · GPU 41 MB |
| idem | Baixa | 60 | 20 ms | 182 | 127 mil | — |
Evidências: `docs/desktop-migration/evidence/desktop_selftest_office_hq_{high,low}.json`. **Não medido:** GPU
integrada modesta e Windows físico (pendente de hardware — A13).

## 7. Capturas
`docs/desktop-migration/screenshots/final/` — 16 telas × tema escuro/claro (`dark-*`, `light-*`), sede e 7 salas em
foco (`office-*`). Auditoria: `screenshots/after/12-auditoria-1440x900.png` (filtrada pela proposta do teste: nunca
mostra contas reais). Para refazer: `AIC_CAPTURE=1 npx playwright test e2e/capture` (em `apps/web`).

## 8. Achados da auditoria independente — situação no código final
| ID | Situação |
|---|---|
| A01 WS com sessão revogada | Corrigido (revalida a sessão; fecha com 4401) — teste |
| A02 teto de IA | Corrigido e **estrito**: reserva pelo limite superior, raciocínio incluído, falhas pós-resposta contabilizadas, retomada não zera — testes adversariais |
| A03 cache entre provedores | Corrigido — teste |
| A04 lease de reconciliação | Corrigido — teste de concorrência |
| A05 KPIs | Corrigido + composição explícita; **definição de negócio** fica com o contador |
| A06 schema da saída | Corrigido — teste |
| A07 retomada | Corrigido com **queda real do processo** em 8 fronteiras |
| A08 13 agentes | 13 ENGINEERING_READY + MOCK_EVALUATED; qualidade com modelo real pendente (chave do proprietário) |
| A09 arte da sede | Poses e estados polidos; **aceite artístico do proprietário** pendente |
| A10 aceite nativo | Autotestes no app macOS real + instalação Windows no CI; **aceite visual no app instalado** pendente (roteiro em `VALIDACAO_FINAL_DESKTOP.md`) |
| A11 servidor/CSP | http remoto recusado no app e no cofre (testes). `connect-src https:` mantido por necessidade (endereço do servidor é configurável) — risco aceito |
| A12 foco | Corrigido — E2E |
| A13 desempenho no hardware-alvo | Mac medido; hardware modesto/Windows físico pendente |
| A14 mapa de departamentos | ADR 0005 (sala segue o backend) |
| A15 busca semântica | Embedder semântico pronto (desligado) + avaliação com gabarito no CI; ligação depende da chave |
| A16 contratos | Woo real verificado contra WooCommerce de verdade; ML com API falsa (falta a aplicação); Shopee/TikTok aguardam acesso de parceiro |
| A17 base rastreável | Tags de base e de entrega; CI por commit |
| A18 infraestrutura | Banco/Redis só em loopback; backup/restauração no CI; cópia externa e monitoramento prontos; staging depende da VPS |
| A19 pose | Corrigido — rotação real dos membros em 7 estados |
| A20 autoteste no release | Verificado: ausente do bundle release |
| A21 erro × vazio | Corrigido |

## 9. Checklist
Modelo novo (schema 3) separa, por tarefa: engenharia, testes, testes nativos, revisão de código, **seu aceite**,
dependências externas e homologação. Painel: engenharia **69/74** concluída com evidência (5 parciais dependem de
escolha/acesso seu), **41** revisões suas, **40** itens bloqueados por homologação externa. Nenhum `status` foi
promovido a DONE (só o proprietário conclui). A divergência "13 concluídas" não existe em nenhuma versão versionada
do checklist (histórico de runs sempre 0 DONE; auditoria registra 0 DONE): vinha de **edições locais no navegador**
(a página permite marcar estados sem gravar o arquivo).

## 10. Limitações e riscos conhecidos
- Qualidade das respostas dos agentes com o **modelo real não avaliada** (decisão: sem IA paga até a produção).
- Shopee/TikTok: contratos de API não verificados (portal de parceiro). Ads, transportadora e emissor de NF-e reais
  dependem da escolha do fornecedor de serviço.
- Sem certificado de código: macOS pede permissão ao Keychain a cada versão nova e mostra "desenvolvedor não
  identificado"; Windows mostra SmartScreen.
- Chave do updater existe só no Mac do proprietário (`infra/env/secrets/`): perdê-la impede atualizações automáticas.
- CSP do app permite `https:` em `connect-src` (servidor configurável pelo usuário).
- Desempenho 3D não medido em GPU modesta/Windows físico.
- Custo de embeddings da avaliação da base é contabilizado, mas a avaliação real só roda com a chave.

## 11. Pendências que não são de engenharia
Ver `docs/PENDENCIAS_EXCLUSIVAS_PROPRIETARIO.md` (contas, documentos, certificados, VPS, aceites) e
`docs/VALIDACAO_FINAL_DESKTOP.md` (roteiro de aprovação do app instalado).

## 12. CI do commit entregue (registro sem omissões)
| Commit | Workflow | Resultado | Causa / correção |
|---|---|---|---|
| `30b2507` (tag `finalizacao-tecnica-2026-10-25`) | ci | **falhou** (1 teste) | A trava nova de IA paga recusa provedor pago quando `CI=true` — o teste antigo `test_llm_google.py::test_settings_and_routing` montava uma configuração Gemini (só para conferir o roteamento) e caiu nela. A trava está certa; o teste passou a simular máquina fora do CI. Reproduzido localmente com `CI=true` e corrigido. |
| `30b2507` | desktop-windows | passou | instalar → abrir (WebView2) → desinstalar no windows-2025 (run 40203339546) |
| `00591d2` (tag `finalizacao-tecnica-2026-10-25.1`) | ci | **falhou** (passo da auditoria dos agentes) | Testes da API todos verdes. O passo separado `agent_audit` rodava num banco recém-criado, sem anúncio/pedido/missão: os casos de 6 agentes foram pulados e o auditor acusou "sem custo/histórico" por falta de dados (não por defeito). Correção: o relatório oficial passa a ser gerado por `tests/test_agent_audit.py`, que cria esses dados pelo fluxo real; o passo separado saiu do CI. |
| **`ea9bc50` (tag `finalizacao-tecnica-2026-10-25.2`)** | ci | **passou** — 4/4 jobs (api, web, progress, compose com smoke, carga, backup/restauração, caos, Trivy, promtool, amtool e Playwright) — run 40205639122 | versão entregue |
| `ea9bc50` | desktop-windows | não disparado (commit não altera o app) | o código do app é o mesmo do `30b2507`, verde no Windows (run 40203339546) |
| `9ce75c5` (finalização do desenvolvimento) | ci | **falhou** (1 E2E no job compose) | O limite de 15 s para a carga da cena 3D, acrescentado nesta rodada, não vale para o executor do CI: lá o WebGL roda em CPU (SwiftShader) e a carga levou 26 s. Os outros 31 E2E e as comparações visuais passaram. Correção: a carga em SwiftShader passa a ser informativa (como o FPS); o limite (90 s) só pega travamento. A medida real é a do app nativo (§14). |
| `9ce75c5` | desktop-windows | passou | run 40209979777 |
| **`f3cf69a` (desenvolvimento + perfil PF)** | ci | **passou** — 4/4 jobs, run 40213990083 | código validado desta fase |
| `f3cf69a` | desktop-windows | **passou** — run 40213990303 | — |

## 13. Finalização do desenvolvimento (25/10/2026, tarde) — o que mudou
Sem reconstruir nada. Cada defeito foi reproduzido, corrigido com a menor mudança e coberto por teste.
| Achado | Causa | Correção | Teste |
|---|---|---|---|
| Aprovações parecidas ("Publicar anúncio" repetido) | a lista só tinha ação/agente/tipo | resumo derivado da proposta (objeto, canal, detalhe, motivo; o que não existe fica vazio) e nível de risco | `test_approval_summary.py`, E2E de aprovação |
| "O que precisa de mim" difícil de achar no agente | não existia | cartão "Precisa de você" no workspace (abre a Central já com a proposta selecionada, sem decidir) e "O que sabe fazer" | E2E de aprovação |
| Erro temporário do provedor derrubava a tarefa | `LLMTransient.retryable` sem uso | até 2 novas tentativas com espera; cada uma passa por orçamento/reserva e fica cobrada | `test_agents_matrix.py` (60 cenários, 13 agentes) |
| Missão limitada a 4 etapas, sem Clara/Nova | limite e plano simulado | até 6 etapas; o plano simulado inclui Clara (regras) e Nova (canais) | `test_e2e_journey.py` (25 passos) |
| Nomes e placas de sala sobrepostos/sobre personagens | placas sem desvio | `lib/plates.ts`: placas de agente sobem; placas de sala tentam posições próximas; corpo dos personagens é obstáculo | `tests/plates.test.ts`, E2E de rótulos |
| **Páginas longas cortadas, sem rolagem** (Configurações, Financeiro) | raiz fixa com `overflow: hidden` e página sem altura limitada: medido 1.101 px de conteúdo em 812 px visíveis, roda do mouse sem efeito | página com a altura da área principal; grade da `split-page` pelo conteúdo | E2E "páginas longas rolam com a roda do mouse" |
| Canais aberto direto não buscava homologações | carga presa às permissões do 1º render | dependência das permissões | E2E PF (abre Canais direto) |
| Workspace do agente rebuscava dados a cada evento do escritório | efeito dependia do contexto inteiro | dependências específicas | revisão de código |

## 14. Adaptação Pessoa Física (CPF) — ADR 0006
Resumo em [`PERFIL_PESSOA_FISICA.md`](PERFIL_PESSOA_FISICA.md): perfil `PERSON` (padrão) / `COMPANY`; matriz de
elegibilidade por canal e aspecto (todos `UNVERIFIED` — só fontes secundárias); GoLivePolicy com perfil obrigatório;
aprovações valem só para o perfil; parecer obrigatório em PF; mudança de perfil/elegibilidade derruba aprovações;
CPF não é armazenado; agentes recebem o perfil como fato (red-team 19/19). Migração `0021` preserva registros.
Testes: `test_seller_profile.py` (11, incluindo tentativas de contornar), `test_golive.py`/`test_gates.py` (PJ
explícito), `test_migrations.py` (0021), E2E "PF/CPF". DRY_RUN continua igual; LIVE continua bloqueado.
Resultados dos testes e do CI do commit final: [`ENTREGA_DESENVOLVIMENTO.md`](ENTREGA_DESENVOLVIMENTO.md) §6–7.

## 15. STATUS DE ENCERRAMENTO — DESENVOLVIMENTO APROVADO PELO PROPRIETÁRIO
**Aceite:** [`ACEITE_PROPRIETARIO_2026-10-25.md`](ACEITE_PROPRIETARIO_2026-10-25.md) (25/10/2026). **Versão congelada:**
SHA e CI em [`ENTREGA_DESENVOLVIMENTO.md`](ENTREGA_DESENVOLVIMENTO.md) §7 e na tag `encerramento-desenvolvimento-2026-10-25`.

| Dimensão | Situação |
|---|---|
| **Produto visual e funcional** | **aprovado pelo proprietário** para DRY_RUN (telas, 13 agentes, 7 salas, 3D, direção artística, Tauri, perfil PF, fluxos simulados) |
| **Engenharia** | concluída: 75/75 tarefas técnicas com evidência; ajustes finais de governança feitos (abaixo) |
| **Testes aprovados** | API 515 (`CI=true`), web 80, E2E 34, evals 17/17 + red-team 19/19, auditoria 13/13 agentes; autoteste nativo macOS 60 FPS no `f3cf69a` (app sem mudança depois) |
| **Testes de hardware pendentes** | só desempenho do 3D em GPU integrada modesta (hardware indisponível). Windows físico **dispensado pelo proprietário** em 25/10/2026; o instalador Windows passa no CI (instalar/abrir/desinstalar) |
| **Homologações externas** | não realizadas: `LEG-01`, `TAX-*`, `SUP-01`, `OPS-01`, `CH-*` (EXT-001…010) |
| **Elegibilidade PF dos canais** | todos `UNVERIFIED` (só fontes secundárias) |
| **Integrações comerciais futuras** | adaptadores reais de Shopee/TikTok, anúncios, transportadora, emissor fiscal; contas ML/Woo/pagamento |
| **IA real** | não avaliada (por regra); `MockLLMProvider` e bloqueio de IA paga no CI mantidos |
| **LIVE** | **bloqueado**; nenhuma operação real executada |

### Ajustes de governança do encerramento (com testes — `tests/test_governance_conditions.py`, 10 casos)
1. **CONDITIONAL ≠ autorização irrestrita:** condições estruturadas (`ufs`, `fulfillment_modes`, `supplier_origins`,
   `product_ids`, `supplier_ids`) conferidas contra a operação; violada, não comprovável, desconhecida ou ausente →
   DENY; vale na revalidação imediatamente antes da execução (`executor._preflight → approvals.reevaluate`), com os
   motivos detalhados na auditoria da falha. Migração `0022`.
2. **Mudança material do perfil:** UF/modelo fiscal/restrições/tipo derrubam só os gates que dependem da propriedade
   (auditado: `seller.profile.updated` + `gate.revoked`); mudança igual/irrelevante não derruba; aprovação guarda
   `profile_fingerprint` e não é reutilizada se o perfil mudar por qualquer caminho.

### Desvios arquiteturais aceitos ([ADR 0007](adr/0007-desvios-aceitos-e-autenticacao.md))
Motor próprio de orquestração (em vez de LangGraph), 3D procedural em R3F (em vez de Blender/GLB, com o exportador
GLB preservado), autenticação própria com MFA e Keychain/Credential Manager (em vez de OIDC/PKCE) — revisão de
segurança sem vulnerabilidade material; limitações documentadas.

**Atualização (25/10/2026, após o aceite):** o proprietário dispensou o teste em Windows físico. 17 tarefas
verificadas no app nativo do Mac foram concluídas; seguem em revisão só as 7 que medem desempenho de renderização
(GPU integrada modesta). Checklist: **36/85 DONE**. As menções a "Windows físico" nas seções 6–10 são o registro
histórico de antes desta decisão.

