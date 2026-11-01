# AI COMMERCE — SDD ADITIVO: DESKTOP EXPERIENCE & 3D OFFICE

**Versão:** 3.0-DESKTOP (aditivo ao SDD v2.0)  
**Data:** 24/10/2026  
**Destino:** Claude Code — reestruturação incremental de uma aplicação já em desenvolvimento  
**Estado:** especificação de implementação; o repositório local ainda precisa ser auditado  
**Escopo:** aplicativo desktop do proprietário, redesenho integral da interface administrativa, visualização de agentes, escritório 3D e distribuição local  
**Não modifica:** regras comerciais/fiscais, governança e aprovações, contratos dos agentes, serviço financeiro, homologações, marketplace connectors e fonte autoritativa de dados do SDD v2.0.

> **MANDATO:** a superfície principal de administração deixa de ser um site aberto no navegador e passa a ser um **aplicativo desktop instalável**, com identidade própria, ótima usabilidade e escritório 3D estilizado e interativo. Preservar e reutilizar o backend/serviços/infraestrutura existentes. Nenhuma função funcional é descartada pela migração. A loja voltada aos compradores continua sendo um storefront web (WooCommerce ou plataforma eleita): **não confundir loja de clientes com aplicativo administrativo**.

---

## 0. Princípios obrigatórios para Claude Code

1. **Auditar antes de alterar.** Inspecionar `package.json`, rotas, UI atual, arquitetura da API, autenticação, WebSockets, contratos, código do 3D, assets, testes, Docker/CI e checklist v2. Identificar o que está concluído e funciona; não alegar conhecer o estado sem leitura do repositório.
2. **Preservar.** Criar branch/commit de segurança, registrar inventário de telas e contratos. Não eliminar tabelas, migrações, integrações, endpoints ou serviços de negócio. Refatorar por componentes, usando adaptadores e feature flags; não reescrever backend só porque a interface é ruim.
3. **Aplicativo real.** Entregar executável desktop nativo (instalador de Windows e, quando houver ambiente de build adequado, pacote macOS); executável que abre janela própria sem exigir que o usuário abra endereço local no browser. Dev mode por navegador é apenas ferramenta de desenvolvimento.
4. **Design de produto, não admin template.** A interface atual será substituída por um sistema visual unificado. Evitar mosaico de cards genéricos, texto pequeno sem hierarquia, cores arbitrárias, tabelas apertadas, ícones sem legenda e dezenas de botões concorrentes.
5. **3D relevante.** Escritório com direção de arte coesa, ambientes distintos, avatares identificáveis, câmera intuitiva e ações reais. Não aceitar cubos/esferas sem acabamento como resultado final. O 3D é representação dos eventos do backend, não simulador de trabalho inventado.
6. **Sem ilusões de funcionalidade.** Estados reais do backend ou DEMO inequivocamente sinalizado. Botões, menus, atalhos, links e ações precisam funcionar; sem mock disfarçado, porcentagem artificial ou 'coming soon' usado para marcar tarefa DONE.
7. **Melhoria verificável.** Medir fluxos críticos por testes e screenshots; registrar itens e evidências no checklist a cada run. Uma tela funcional mas ilegível, inconsistente ou inacabada visualmente **não** passa na aceitação.
8. **Nunca promover live automaticamente.** As regras de `DRY_RUN`, autorização financeira, homologações fiscais e de marketplaces do SDD v2 continuam vigentes.
9. **Executar por fatias integradas.** Entregar shell desktop, sistema de design, navegação, visualização de agentes, dashboard e escritório 3D em incrementos completos; não remover módulos nem interromper agentes em funcionamento.
10. **Adotar APIs estáveis.** Usar versões compatíveis efetivamente instaladas e documentação oficial; não adicionar bibliotecas sem examinar conflitos, manutenção e impacto de bundle.

---

## 1. Diagnóstico do problema e resultado desejado

### Sintomas reportados pelo proprietário
- Frontend atual considerado visualmente ruim e difícil de entender.
- Visualização e gestão dos agentes pouco claras.
- Qualidade do escritório 3D ainda não confirmada; expectativa de visual **legal e divertido**, sem necessidade de realismo.
- A intenção é um **aplicativo na máquina**, não um painel administrativo hospedado e acessado como site.

### Resultado obrigatório
- Ao abrir o programa: em até poucos segundos, saber **o que está acontecendo**, **o que precisa de aprovação**, **quanto foi gasto/recebido** e **o que os agentes estão fazendo**.
- Fluxos de controle equivalentes pelo modo 2D e, quando aplicável, pelo 3D.
- Aplicativo que parece um produto desktop coeso, com janelas/menus/atalhos e integração de sistema operacional adequada.
- Escritório 3D com estilo de jogo de gerenciamento isométrico, expressivo e coerente com o domínio de e-commerce.
- Backend segue operando na VPS quando o aplicativo estiver fechado. App apenas supervisiona e solicita ações autorizadas.

### Não objetivos
- Não criar um novo checkout ou loja voltada ao consumidor dentro do aplicativo.
- Não migrar banco de dados e serviços essenciais para a máquina local por padrão.
- Não desenvolver jogo de mundo aberto, multiplayer, missões fictícias ou física complexa.
- Não substituir código confiável de execução de agentes por animações de frontend.
- Não alterar regras fiscais, financeiras ou políticas de marketplace para facilitar uma demonstração.

---

## 2. Pesquisa de referências e decisões de design

| Referência | O que aproveitar | O que NÃO copiar |
|---|---|---|
| Linear — UI refresh (2026) | Navegação consistente, sidebar contida, foco no conteúdo, itens fáceis de escanear, inbox de prioridade | Identidade visual, ícones e layout literal |
| Fluent Design / Windows Guidelines | Hierarquia, espaçamentos, ações explícitas, suporte a teclado, componentes nativos coerentes | Aparência genérica de painel corporativo |
| Two Point Hospital / Two Point Campus | Composição isométrica, salas reconhecíveis, animações sutis de equipe, atmosfera divertida | Assets, personagens, nomes e telas de jogo proprietários |
| Tiny Glade (atmosfera, não mecânica) | Materiais acolhedores, iluminação suave, pequenos detalhes de cenário | Ambiente medieval ou realismo obrigatório |
| React Three Fiber / Drei | Reaproveitamento declarativo dos componentes 3D, qualidade adaptável, picking, controle de câmera | Tecnologias alpha obrigatórias no MVP |

**Referências oficiais consultadas (24/10/2026):**
- https://linear.app/changelog/2026-03-12-ui-refresh
- https://learn.microsoft.com/en-us/windows/apps/design/guidelines-overview
- https://fluent2.microsoft.design/accessibility
- https://v2.tauri.app/start/frontend/nextjs/
- https://v2.tauri.app/security/capabilities/
- https://v2.tauri.app/reference/webview-versions/
- https://v2.tauri.app/plugin/updater/
- https://www.electronjs.org/docs/latest/tutorial/security
- https://r3f.docs.pmnd.rs/advanced/scaling-performance
- https://drei.docs.pmnd.rs/performances/adaptive-dpr

**Nota:** referências visuais são inspiração; desenvolver assets e sistema de design originais, inclusive observando licenças.

---

## 3. Escolha do runtime desktop — ADR-DESK-001

### Decisão principal: Tauri 2 + frontend React + API remota FastAPI

A VPS permanece responsável por agentes, workflows, acesso aos marketplaces, dados, logs, autorização e geração/armazenamento de eventos. O desktop executa UI 2D + cena WebGL 3D, recebe dados por HTTPS/WSS, permite configuração local não sensível e gerencia sessões com segurança.

**Motivação:** aproveita React/TypeScript existentes; app instalável sem distribuir backend Python no computador; runtime compacto que usa webview do sistema; suporte a atualizações assinadas. Em Tauri 2: Windows usa WebView2, macOS usa WKWebView — testar a cena 3D em ambas as plataformas-alvo.

**Plano alternativo documentado:** Electron se os testes de WebGL 3D, compatibilidade do frontend atual ou periféricos nativos reprovarem os critérios de aceite no runtime Tauri em máquinas-alvo. Electron exige configuração segura (`contextIsolation`, sandbox, `nodeIntegration: false`, CSP, protocolo seguro e validação de IPC). Não mudar runtime apenas por preferência subjetiva.

**Ponto importante sobre Next.js:** Tauri não hospeda SSR Next dentro do binário por padrão. Primeiro auditar o frontend atual:
- Se páginas e componentes funcionarem via exportação estática e API remota, preservar Next.js com `output: 'export'` e `frontendDist` apontando para `out`.
- Se houver dependência real de SSR, route handlers, Server Actions ou middleware Next para o painel, extrair essa lógica para o FastAPI (ou backend autorizado) e migrar **somente o renderer administrativo** para React + Vite, preservando componentes e tipos compartilhados.
- Não embutir servidor Next Node na máquina sem ADR justificando essa complexidade.
- O storefront WooCommerce não é parte desse empacotamento.

### Topologia

```text
                     COMPUTADOR DO PROPRIETÁRIO
  +------------------------------------------------------------+
  | AI Commerce Desktop (Tauri 2)                              |
  | - Janela nativa / menus / atalhos / notificação/tray        |
  | - React UI (Command Center, agentes, workflows, etc.)       |
  | - React Three Fiber (Escritório 3D)                        |
  | - Cache local mínimo criptografado/sem dados financeiros    |
  | - Sessão segura do usuário + estado de conexão             |
  +----------------------------+-------------------------------+
                               | HTTPS / WSS (TLS)
                               v
                          VPS / API
    +---------------------------------------------------------+
    | Reverse proxy → FastAPI → LangGraph + workers + policies  |
    | PostgreSQL, Redis, audit, budgets, commerce, KB, storage  |
    | Adapters: Woo, ML, Shopee, TikTok, Ads, fiscal, suppliers |
    +---------------------------------------------------------+
                               |
                               v
                        PLATAFORMAS EXTERNAS
```

### Requisitos de empacotamento
- Build de desenvolvimento executável via comando documentado (p.ex. `pnpm desktop:dev`).
- Build release via comando documentado (`pnpm desktop:build`), instalador em artefatos do CI.
- Windows: instalador apropriado e testes de instalação/desinstalação; macOS: pacote DMG/app assinado e notarizado quando for distribuído. Não prometer macOS sem runner/assinatura disponíveis.
- Ícone próprio, nome de app, versão, splash breve, comportamento ao minimizar/fechar configurável.
- Menu nativo: File/Editar/Exibir/Ajuda quando pertinente; atalhos de navegação.
- Verificação de update assinada em canal de desenvolvimento e release, sem update remoto executável arbitrário.
- Persistência de janela/monitor/tema com fallback se a posição antiga ficar fora da tela.
- Login com OAuth/OIDC PKCE adequado a desktop; usar browser do sistema quando exigido; tokens armazenados no cofre do SO ou outro armazenamento seguro; jamais armazenar secrets fiscais, chaves de API dos agentes ou admin tokens em frontend/localStorage.
- CSP, Tauri capabilities mínimas, origem/API restrita, checagem de assinatura e proibição de `eval`/código remoto não confiável.
- Offline: mostrar estado de conexão, último snapshot identificado como desatualizado e modo consulta seguro; bloquear aprovações e mutações reais offline. Nunca enfileirar gastos reais offline sem nova autenticação/confirmação.
- App fechado NÃO para workers da VPS; preferências do desktop não alteram execução remota.

### Critério de escolha Tauri x Electron
Comparar os dois somente se necessária exceção, medindo: compatibilidade do app atual, WebGL de teste em hardware-alvo, FPS/memória, tempo de startup, empacotamento, segurança, acessibilidade e manutenção. Registrar ADR e evidências.

---

## 4. Novo conceito visual — 'Cozy Command Center'

### Direção
- **Personalidade:** centro de controle de uma pequena empresa de IA com toque de jogo de gerenciamento, não painel bancário nem cyberpunk saturado.
- **Interface de gestão:** limpa, adulta e clara. O toque divertido fica em ilustrações/ícones/avatares/3D, sem prejudicar leitura.
- **Cena 3D:** diorama isométrico colorido, aconchegante, com móveis e personagens estilizados. Low-poly bem acabado, e não geometria de protótipo.
- **Tonalidade:** séria nas decisões financeiras; leve e calorosa na ambientação.

### Paleta inicial (tokens; ajustar depois de testes visuais)

```css
--app-bg:       #10151D; /* superfície geral */
--panel-bg:     #171F2B;
--panel-raised: #202B3A;
--border:       #304052;
--text-main:    #F1F5F9;
--text-muted:   #AAB8C9;
--accent:       #7DD3B0; /* menta, ações seguras */
--accent-2:     #88A9F3; /* azul suave, seleção */
--warning:      #F4C47F;
--critical:     #F08B8B;
--success:      #76CFA6;
--focus-ring:   #A9C1FF;
```

**Regra:** cor sempre reforçada por rótulo/ícone para status (não confiar em cor isolada). Não usar tons de perigo para métricas comuns. Gráficos devem funcionar para daltonismo.

### Tipografia e escala
- Fonte de UI legível, licença apropriada: `Inter`, `Geist` ou fonte de sistema; títulos e números com tamanhos consistentes.
- Corpo base 14–16 px, labels secundários 12–13 px no mínimo, indicadores principais 24–32 px; não reduzir texto para fazer cards caberem.
- Escala de espaçamento 4/8/12/16/24/32 px, radius 8–14 px, bordas suaves, sombras contidas.
- Painel com largura mínima funcional 1100×700, design útil em 1366×768 e 1440×900; suporte a 1920×1080 e ultrawide sem grandes vazios.
- Dark theme principal; light theme posterior se viável com mesmos componentes e testes de contraste.

### Componentes do design system
`AppShell`, `SidebarNav`, `TitleBar`, `Breadcrumb`, `GlobalSearch`, `CommandPalette`, `StatCard`, `AgentIdentity`, `AgentStatus`, `TaskTimeline`, `ApprovalCard`, `RiskBadge`, `ChannelTag`, `MoneyAmount`, `LiveDemoIndicator`, `DataTable`, `EmptyState`, `ErrorState`, `Skeleton`, `ConfirmDialog`, `DetailsDrawer`, `Toast`, `NotificationCenter`, `FilterBar`, `ChartPanel`, `OfficeOverlayPanel`, `ConnectionStatus`.

Use biblioteca de componentes acessíveis madura (p.ex. Radix/shadcn como base), mas personalizar o design system. Não criar dezenas de componentes divergentes ou tela com UI default sem acabamento.

### Restrições visuais negativas
- Não empilhar cartões KPI sem ordem de importância.
- Não mostrar todos os agentes como miniaturas idênticas em uma grade com nomes minúsculos.
- Não colocar o histórico inteiro do chat em cartões apertados.
- Não usar emoji como avatar definitivo; manter ícones Lucide para UI, avatares 2D/3D autorais.
- Não usar brilho neon, gradientes excessivos, partículas persistentes ou animações aleatórias para parecer moderno.
- Não colocar conteúdo de marketing na navegação administrativa.

---

## 5. Arquitetura de informação e navegação

### Menu principal do aplicativo (fixo, recolhível)

```text
AI COMMERCE                         [Pesquisar... Ctrl/Cmd+K]  [Alertas] [Perfil]
──────────────────────────────────────────────────────────────────────────────
  VISÃO GERAL
    Centro de comando
    Escritório 3D
    Aprovações (N)
  OPERAÇÃO
    Agentes
    Missões & tarefas
    Produtos & pesquisa
    Fornecedores
    Canais de venda
    Campanhas
    Pedidos & logística
  INTELIGÊNCIA
    Financeiro
    Relatórios
    Base de conhecimento
  SISTEMA
    Homologações
    Histórico & auditoria
    Configurações
──────────────────────────────────────────────────────────────────────────────
  [DEMO ou AO VIVO]  [API Conectada]  [Custos IA hoje]  [Versão desktop]
```

### Regras de navegação
- Itens agrupados por objetivo, não por nome interno de serviço.
- Qualquer tela principal alcançável em até 2 cliques a partir da sidebar.
- Título, breadcrumb, status de conexão e ação principal em posição consistente.
- `Ctrl/Cmd+K` abre comando e busca por agentes, produtos, tarefas, páginas e ações permitidas.
- `Ctrl/Cmd+1`: Centro; `+2`: Escritório; `+3`: Agentes; `+4`: Aprovações; `+5`: Financeiro (atalhos só quando janela tem foco, sem captura global indiscriminada).
- Detalhes abrem painel lateral persistindo contexto quando útil; não usar modal gigante para tarefas longas.
- Tabelas com colunas configuráveis, filtro, busca, ordenação, paginação/virtualização e estados vazios explicativos.
- Todas as operações destrutivas/financeiras exigem confirmação conforme backend; UI nunca usa 'aprovar tudo' implicitamente.
- Ações primárias com verbo explícito: 'Revisar proposta', 'Aprovar orçamento', 'Abrir execução', 'Pausar campanha'.

---

## 6. Telas mandatórias e composição detalhada

### UX-01 — Centro de Comando (landing)
Objetivo: responder em menos de 10 segundos: **quanto vendemos, quanto gastamos, o que exige minha decisão e o que os agentes fazem agora?**

Layout 1440×900 (proporções aproximadas):

```text
┌ Sidebar 224 ┐ ┌──────────────── Conteúdo principal ───────────────────────┐
│ Centro      │ │ Bom dia. Sua operação está em [SIMULAÇÃO].   [Criar missão]│
│ Escritório  │ │ Pedidos pagos | Receita | Resultado | Publicidade | IA    │
│ Aprovações  │ │ [3 decisões prioritárias]    [Alertas: estoque / erros]   │
│ Agentes     │ │                                                        │
│ Missões     │ │ MISSÕES EM ANDAMENTO      | AGENTES EM ATIVIDADE          │
│ Produtos    │ │ Linha do tempo + progresso| 4 agentes c/ ação atual       │
│ Canais      │ │                                                        │
│ Financeiro  │ │ MOVIMENTO COMERCIAL        | MINI VISTA DO ESCRITÓRIO 3D  │
│ ...         │ │ Gráfico receitas / custos  | [Abrir escritório]           │
└─────────────┘ └────────────────────────────────────────────────────────┘
```

- Prioridade visual: decisões pendentes, estado da operação, KPIs financeiros essenciais, missões em andamento.
- Dados com fonte, período, moeda BRL e distinção entre realizado, estimado e atribuído.
- Nenhum KPI fictício em LIVE; `DEMO` visível no cabeçalho e nas áreas com dados simulados.
- CTA principal: `Criar missão`; secundário: `Abrir escritório`.
- Botão `Parar novas despesas` acessível, sujeito a confirmação segura.

### UX-02 — Diretório de Agentes
Novo modelo: **diretório legível + detalhes**, não cards indistintos.
- Cabeçalho: `13 agentes · 4 trabalhando · 2 aguardando aprovação · 1 impedido` (valores reais ou demo marcado).
- Filtros por departamento, estado e capacidade; lista/grade alternável.
- Cada entrada: avatar 2D estilizado, nome, função em linguagem humana, departamento, estado com texto, tarefa atual curta, tempo desde última atualização, botão `Abrir agente`.
- Click abre **Agent Workspace** com 4 abas claras: `Visão geral`, `Tarefas`, `Conversas`, `Ferramentas e custos`.
- Tarefas de um agente organizadas por estado com histórico cronológico; nunca apresentar 'pensamentos internos' como logs legíveis.
- Cada agente possui 2D portrait consistente com seu modelo 3D, cor de identificação sutil e silhueta distinta. Nome e ícone sempre acompanham cor.
- Mostrar `Fonte: execução #...`, custo da última tarefa, restrições e últimas entregas; opções perigosas visíveis somente a quem tem permissão.

### UX-03 — Chat com agentes
- Layout semelhante a ferramenta profissional de trabalho: lista de conversas/missões à esquerda, histórico central, contexto da tarefa à direita ou drawer.
- Diferenciar mensagens humanas, explicações do agente, chamadas de ferramenta (sumarizadas) e eventos do sistema.
- Mostrar confirmação de ação externa, aprovação pendente e documentos gerados como componentes distintos.
- Comandos claros: `Pedir análise`, `Ver fonte`, `Abrir tarefa`, `Solicitar revisão`.
- `streaming` quando suportado, com estado de processamento; não prometer resposta em tempo fixo.
- Histórico paginado; permitir copiar/exportar; não expor segredos ou raciocínio interno bruto.

### UX-04 — Missões & Workflow
- Lista/tabela de missões com owner/agents, objetivo, estado, custo acumulado, duração, último evento, bloqueio.
- Detalhe em timeline ou fluxo passo a passo; grafo visual opcional (não obrigatório para entender).
- Estado de espera claro: `Aguardando aprovação`, `Aguardando ferramenta`, `Bloqueado por política`, `Falha recuperável`.
- Retentativa segura limitada pelo backend, cancelamento, reexecução de simulação.

### UX-05 — Central de Aprovações
- Inbox prioritária com distinção `urgente / normal / risco / valor`, contexto de canal/produto.
- Detalhe mostra dossiê, evidências, limite financeiro, mudanças propostas, validade da decisão.
- Approve/Reject/Request changes com justificativa, confirmação e retorno de API; sem aprovações apenas locais.
- Oferecer comparador de versão e limite aprovado versus solicitado; nunca aplicar sem confirmação do backend.

### UX-06 — Produtos e fornecedores
- Catálogo mestre; oportunidades; fornecedor; preço por canal; risco e conformidade.
- Ações progressivas com evidência, integração e aprovação; com imagens de produto carregadas de fonte autorizada.
- Visão comparativa de fornecedores com dados verificados/desatualizados claramente distinguidos.

### UX-07 — Campanhas, canais e pedidos
- Visões operacionais separadas com hierarquia consistente; conexão do canal, atraso de sync, pedidos, custo e métricas.
- Canais Woo/ML/Shopee/TTS com status `Mock`, `Não conectado`, `Conectado`, `Homologação pendente`, `Problema`; evitar indicador genérico verde.

### UX-08 — Financeiro
- Receita realizada, projeções, saldo reservado, despesas de publicidade, custos IA, margem por SKU/canal, recebedores, períodos.
- Cards superiores enxutos e tabela/movimentação abaixo; custo e faturamento nunca confundidos.
- Gráficos com tooltip, legenda e contexto; estados `zero real`, `sem conexão`, `simulado` distintos.

### UX-09 — Base de conhecimento e homologações
- Busca, filtros, fonte, validade, dono da revisão, situação de evidência.
- Checklist de go-live por fornecedor/canal/produto, sem fingir conclusão por clique local.

### UX-10 — Configurações de desktop
- Tema, volume de sons, acessibilidade, qualidade gráfica, notificações, idioma pt-BR, atalhos, inicialização automática opt-in, comportamento ao fechar, versão e diagnósticos de conexão.
- Separar configurações do computador de políticas remotas de dinheiro e contas.

---

## 7. Escritório 3D — DIREÇÃO DE ARTE NÃO NEGOCIÁVEL

### Conceito: 'Mini Empresa Viva'
Uma pequena sede isométrica em formato de **diorama modular**, com 5–7 ambientes separados, paredes em corte ou vidro, mobiliário detalhado, iluminação quente/fria equilibrada e animações leves. A cena deve parecer o interior de um jogo de gerenciamento de boa direção de arte, sem buscar realismo fotorrealista.

**Estilo:** low-poly estilizado / soft-toy, materiais mate, cantos suavizados, AO leve, sombras macias, composição aconchegante e cores por departamento. Não usar texturas genéricas repetidas nem cubos simples como resultado final.

#### Ambientes obrigatórios
1. **CEO/Command Room:** mesa de liderança, painel de aprovações, mapa simplificado do negócio, detalhes pessoais discretos.
2. **Research Lab:** quadros de tendências, prateleiras de amostras, notebooks, pesquisas de Luna e Orion.
3. **Marketing Studio:** monitor de criativos, câmera/softbox estilizados, painéis de campanhas para Iris/Pixel/Vera.
4. **Commerce & Suppliers:** caixas, etiquetas, displays de produtos e telas de fornecedor para Nova/Nexus/Dante.
5. **Finance & Compliance:** telas com símbolos financeiros, arquivos, livros e pequenos displays para Finn/Clara.
6. **Operations & Support:** quadros de envios, tracking e atendimento para Logan/Eva.
7. **Meeting Hub:** mesa redonda e painel de apresentações de Atlas; reuniões visuais só com eventos reais/solicitados.

> Departamentos podem compartilhar salas e personagens podem mover-se entre elas por eventos de tarefa. Todos os 13 agentes possuem perfil visual, ainda que só parte deles apareça fisicamente ao mesmo tempo por performance.

### 7.1 Avatares e identidade
- Os 13 agentes devem ser identificáveis **sem precisar ler uma tooltip longa**: silhueta, roupas, acessório, penteado, cores sutis, posição e nome visível ao selecionar.
- Criar uma **character bible** original (uma página por agente): nome, função, shape language, roupas, 2D portrait, paleta secundária, acessório, idle, working, special action, restrições de animação.
- Altura e escala consistentes, acabamento não realista; preferir meshes low-poly com materiais coesos.
- Não misturar avatares realistas, cartoon 2D flat, emoji e humanoides 3D aleatórios.
- Reaproveitar rig/animações-base, mas fazer variações de personagem e comportamento.
- Asset importados precisam de licença confirmada e arquivo de atribuição.

### 7.2 Estados e animações
| Estado do backend | Sinal visual | Animação permitida |
|---|---|---|
| `IDLE` | Nome e badge disponível | Respiração, olhar ao redor, gestos sutis |
| `PLANNING` | Indicador de análise | Olhar tela/prancheta, pensamento discreto |
| `WORKING` | Tarefa com texto resumido | Digitação ou interação relevante |
| `TOOL_CALL` | Ícone de ferramenta temporário | Verificação de tablet/tela |
| `WAITING_APPROVAL` | Ícone de decisão no agente/sala CEO | Gesto curto de pedido, sem loop irritante |
| `BLOCKED` | Badge e motivo acessível | Pausa/hesitação discreta |
| `COMPLETED` | Notificação pontual | Pequena celebração 1 vez, não confetes constantes |
| `ERROR` | Alerta com explicação | Animação mínima, sem vermelho piscando |

- Animações **não** são prova de tarefa real; acoplamento obrigatório a `agent_presence` / `workflow_events`.
- Nunca usar aleatoriedade para trocar estados ou criar tarefas fictícias.
- Procedural idles só podem ocorrer dentro de estados reais.

### 7.3 Interações 3D
- Câmera isométrica ortográfica (ou perspectiva fraca) com órbita limitada, zoom suave, pan por mouse e atalhos `R` reset camera.
- Clique em personagem: highlight, nome, status e **drawer HTML** com tarefa, últimas entregas, botões `Conversar`, `Abrir tarefas`, `Ver custos`, `Ir ao departamento`.
- Clique em sala: foco suave de câmera + painel com trabalhos daquele departamento.
- Duplo clique em avatar (opcional): abrir workspace completo, não modal no canvas.
- Hover sinaliza seleção; cursor padrão/interativo, limites de câmera, pointer capture correto, sem cliques atravessando UI.
- Ícones contextuais discretos; evitar balões que encobrem o diorama.
- Alternância `Escritório 3D / Lista de Agentes` persistente; equivalente funcional via 2D.
- Badge de eventos importantes no topo (ex.: aprovações), não inundar cena de textos.
- Sons sutis opt-in, mute no app, respeitar silêncio e `prefers-reduced-motion`.

### 7.4 Asset pipeline
```text
assets/office/
  art-bible.md
  licenses.md
  source-blender/*.blend
  exports/rooms/*.glb
  exports/props/*.glb
  exports/characters/*.glb
  textures/ (compressed if supported)
  thumbnails/
```
- Blender como fonte de arte, exportar glTF/GLB otimizado.
- Separar kit modular de parede/piso/móveis, salas, props e personagens.
- Compressão geometry/material/texture conforme compatibilidade validada no renderer.
- LOD/culling e instancing em props repetidos; geometria compartilhada e draw calls reduzidos.
- Animações originais com clips e estados controláveis no código.
- Todos os 3D assets acompanhados de preview/thumbnail, tamanho final e origem/licença.
- Não incluir GLBs ou fontes binárias enormes desnecessárias no instalador; asset packaging versionado e cache seguro se remoto.
- Asset loading com progresso, fallback 2D e erro claro.

### 7.5 Iluminação, câmera, render
- Key light suave + preenchimento, sombras moderadas, AO com limites de performance; tons acolhedores e legibilidade dos objetos.
- Composição que se lê a 100% de zoom em 1366×768; personagens facilmente distinguíveis.
- Piso/base do diorama com profundidade; poucos detalhes vivos: plantas, luminárias, monitores, posters, caixas.
- Efeitos de pós-processamento só se agregarem valor medido; preferir materiais/iluminação cuidadosos a bloom excessivo.
- Textos, gráficos, botões e logs comerciais renderizados em DOM acessível, nunca exclusivamente em texturas 3D.

### 7.6 Performance e suporte gráfico
Metas iniciais **a validar em hardware-alvo**, não garantias antecipadas:
- GPU integrada atual / 1366×768: alvo >=30 FPS estável em nível 'baixo' com 6 agentes animados e cena essencial.
- Desktop intermediário / 1080p: alvo >=50 FPS mediano e interação responsiva em 'médio/alto'.
- Tempo para primeira interação da cena: alvo <5 s com assets já em cache, rede normal (medir); skeleton/loading enquanto carrega.
- Memória e draw calls monitoradas em dev tools; orçamento de assets estabelecido após primeiro vertical slice com medição.
- Qualidade `Low / Balanced / High / Auto`: DPR dinâmico, sombras desligáveis, agentes secundários com animação reduzida, instancing, frustum culling e lazy loading.
- Suspender ou reduzir renderização quando aba 3D não estiver ativa ou janela estiver minimizada.
- Se WebGL falhar ou driver insuficiente: modo 2D acessível, sem crash ou bloqueio de aprovações.
- Teste obrigatório em **WebView2 Windows** e, se macOS suportado, **WKWebView** com cenário 3D real.

### 7.7 Critérios visuais para aceitar o 3D
O trabalho **não** estará DONE se apenas mostrar caixas e cilindros como 'escritório'. Exigir:
- 6 salas identificáveis, visualmente coerentes, com móveis e iluminação acabados.
- 13 identidades de avatar coerentes; no MVP do redesenho ao menos 5 avatares visivelmente únicos polidos e pipeline escalável para os outros; fase final entrega todos.
- Câmera, hover, clique, foco, drawer, interação, reload/reconnect e status real testados.
- 3 estados de animação por avatar relevante (`idle`, `working`, `awaiting`), com transição sem saltos grosseiros.
- Screenshot comparativa em três enquadramentos: escritório completo, seleção de agente, sala destacada.
- Revisão humana visual de qualidade e legibilidade. Se o resultado parecer protótipo de geometria, polir arte antes de marcar DONE.

---

## 8. Modelo de dados, contratos e sincronização

Reutilizar `agent_presence`, `workflow_events`, tasks, approvals, finance do SDD v2. Não tornar UI o sistema autoritativo. Tipos TS gerados de OpenAPI ou de esquemas validados; evitar shape diferente por tela.

**Contrato recomendado:**

```ts
type RunMode = 'DEMO' | 'LIVE';
type AgentState = 'IDLE' | 'PLANNING' | 'WORKING' | 'TOOL_CALL'
  | 'WAITING_APPROVAL' | 'BLOCKED' | 'COMPLETED' | 'ERROR';
interface AgentPresence {
  agent_id: string;
  name: string;
  role: string;
  department_id: string;
  state: AgentState;
  active_task_id?: string;
  active_task_title?: string;
  last_event_at: string;
  event_seq: number;
  run_mode: RunMode;
}
interface DesktopEvent {
  id: string;
  seq: number;
  type: string;
  occurred_at: string;
  entity_id?: string;
  entity_version?: number;
  payload: Record<string, unknown>;
}
```

**Regras:**
- Um `PresenceStore` normalizado alimenta diretório, cards, mini-map e cena 3D.
- Conectar WSS autenticado; em reconexão buscar snapshot + eventos após último `seq`; aplicar deduplicação e rejeitar `seq` anterior.
- Título de tarefa e estado devem vir do backend; `last_event_at` permite mostrar 'dados de 2 min atrás'.
- API `GET /v1/agent-presence/snapshot`, `WS /v1/office/events` existentes ou adapters equivalentes.
- 3D usa somente campos visuais derivados; alterar pose ou abrir drawer não tem efeitos externos.
- Erro de autenticação/sync mostra banner e tenta reconexão com backoff; não mostrar como 'trabalhando' indefinidamente após desconexão.
- `DEMO` e `LIVE` não podem misturar datasets no mesmo KPI.

### Desktop shell state
`window_bounds`, `display_id`, `theme`, `3d_quality`, `mute_sfx`, `sidebar_collapsed`, `last_active_view`, `reduced_motion` armazenados localmente com versionamento/migração. Credenciais só no cofre seguro.

---

## 9. Segurança e operação remota

- O desktop não acessa PostgreSQL, Redis, gateway de pagamentos ou APIs de marketplaces diretamente.
- Todas as operações passam pela API com RBAC, auditoria e regras do SDD v2.
- `https://` e `wss://` para VPS; TLS válido, headers e políticas explícitas para origem desktop.
- CORS não é controle de autorização; validar JWT/sessão no backend em toda operação.
- OAuth/OIDC para desktop com PKCE; refresh tokens protegidos pelo sistema operacional; logout limpa tokens e cache sensível.
- Código HTML/Markdown produzido por agente é sanitizado e não tem privilégio desktop.
- Não habilitar shell, file system, clipboard ou notificações além das capabilities estritamente necessárias.
- Links externos abrem no navegador padrão após validação de esquema/host.
- Crash do aplicativo não invalida atividades remotas ou autorizações; pendências retomam pela API.
- Auto-updater Tauri usa artefatos assinados com chave privada fora do app e rollback/estratégia de recuperação testada.
- Notificações locais para `WAITING_APPROVAL`, `ERROR` e alertas críticos somente com opt-in e sem vazamento de dados sensíveis na tela bloqueada.
- Tray mostra apenas status. Se app fechar: agentes na VPS continuam; se preferido minimizar para tray, opção configurável e transparente.

---

## 10. Migração incremental do frontend existente

### Fase 0 — auditoria/backup (obrigatória)
Executar sem destruir: identificar runtime atual, mapas de rotas, componentes, estilos, testes, dependências, bibliotecas 3D, serviços, integrações; colher screenshots reais de cada tela e defeitos encontrados. Produzir:
- `docs/desktop-migration/current-state-audit.md`
- `docs/desktop-migration/screenshots/before/*`
- `docs/desktop-migration/route-map.md`
- `docs/desktop-migration/component-reuse.md`
- `docs/adr/ADR-DESK-001-runtime.md`

Registrar quais contratos foram preservados, riscos e comandos de execução. Nunca afirmar 'frontend ruim por X' sem inspeção.

### Fase 1 — shell desktop funcional
- Criar `apps/desktop/` Tauri com build e ícone.
- Reusar client API/autenticação, serviços de estado e integração com VPS.
- Criar `AppShell`, navegação estável, status conexão, sessão, toggle DEMO/LIVE somente permitido, atualização de tela.
- Empacotar aplicativo e rodar na máquina de desenvolvimento (ou indicar impossibilidade por ambiente com evidência).
- No início, renderizar telas antigas em módulos migrados se necessário; não interromper workflows.

### Fase 2 — design system e Command Center
- Criar tokens, componentes, ícones e fontes, mocks explicitamente rotulados quando usados em storybook/demo.
- Montar Centro de Comando com informação hierarquizada; capturar screenshots em resoluções alvo.
- Construir templates para tabelas, drawers, error/empty states e skeletons.

### Fase 3 — Agentes (prioridade visual máxima)
- Criar art direction e portraits.
- Refazer Diretório, Workspace do Agente, Chat e Workflow.
- Garantir agentes identificáveis, atividades reais, histórico, custos e botões corretos.
- Cobrir fluxo 'agente → tarefa → entrega → aprovação'.

### Fase 4 — 3D vertical slice de alta qualidade
- Produzir **1 sala 100% acabada, 2 avatares acabados e um fluxo completo de seleção/estado**, primeiro.
- Revisar qualidade visual e medir FPS no runtime desktop; corrigir arte e perf antes de multiplicar salas.
- Transformar kit de assets em 6–7 ambientes; completar 13 avatares e integrações.
- Evitar repetir uma cena feia em escala maior; aprovação de visual é gate.

### Fase 5 — redesenho dos módulos restantes
Aprovações, Financeiro, Produtos, Fornecedores, Canais, Campanhas, Pedidos, KB, Homologações, Admin. Reusar backend e tipos; manter todos os fluxos originais e testes funcionais.

### Fase 6 — empacotamento e QA
Teste instalação, atualização, sessão, offline, segurança, WebGL, chamadas de API, regressão 2D, 3D, acessibilidade e desempenho. Registrar screenshots `after`.

### Regra de fallback e corte
Somente remover a UI anterior quando **paridade funcional** comprovada + release desktop funciona + rollout testado. Não apagar código antigo sem verificação e migração de rotas/fluxos. Se algo ainda não couber no release, expor claramente como `BLOQUEADO` e documentar; não esconder tarefa incompleta.

---

## 11. Checklist de implementação / acompanhamento

O SDD v2 já prevê `progress/tasks.json`, `progress/checklist.html`, `progress/runs.jsonl` e `scripts/update_progress.py`. **MANTER ESSE CONTRATO SEM CRIAR UM SEGUNDO CONTROLE DE PROGRESSO**.

### Novos IDs de tarefa (aditivos, estáveis)

| ID | Entrega e evidência mandatória |
|---|---|
| UXD-001 | Auditoria do frontend, screenshots before e mapa de telas |
| UXD-002 | ADR desktop Tauri/Electron e compatibilidade com Next/Vite |
| UXD-003 | App Tauri dev e instalador desktop testado |
| UXD-004 | Autenticação desktop, token seguro e sessão remota |
| UXD-005 | Design system, tokens, componentes, tema e acessibilidade |
| UXD-006 | Sidebar, titlebar, atalhos e command palette |
| UXD-007 | Centro de Comando com KPIs reais/demo sinalizados |
| UXD-008 | Diretório dos 13 agentes legível e filtrável |
| UXD-009 | Agent Workspace, chat, tarefas e custos |
| UXD-010 | Missões e timeline de workflows |
| UXD-011 | Inbox/detalhes de aprovações reestilizados |
| UXD-012 | Produtos, fornecedores e canais reestilizados |
| UXD-013 | Campanhas, pedidos e logística reestilizados |
| UXD-014 | Financeiro, relatórios, KB e homologações reestilizados |
| UXD-015 | Character Bible e art bible 3D aprovadas |
| UXD-016 | Vertical slice 3D: sala acabada + 2 avatares + interação |
| UXD-017 | Escritório 6–7 salas com detalhes, luz e câmera polidas |
| UXD-018 | Treze personagens com identidades e clips visuais |
| UXD-019 | Sincronização evento/estado 2D ↔ 3D e reconexão |
| UXD-020 | Qualidade gráfica adaptável, performance e fallback 2D |
| UXD-021 | Menus desktop, notificações, tray, offline e atualização |
| UXD-022 | Testes E2E de fluxos críticos com screenshots before/after |
| UXD-023 | Revisão UX/acessibilidade e correção dos blockers |
| UXD-024 | Builds assináveis, documentação de instalação e rollback |

**Categorias novas:** `DESKTOP_FOUNDATION`, `UX_2D`, `AGENT_UX`, `OFFICE_3D`, `DESKTOP_QA`. Não alterar IDs anteriores. Vincular tarefas antigas de frontend e 3D a essas subtarefas sem marcar DONE automaticamente ou duplicar o progresso.

### Regra obrigatória AO FINAL DE TODO RUN do Claude Code
1. Comparar commits/diffs e validar tarefas alteradas com testes e screenshots.
2. Editar `progress/tasks.json` com status compatível, notes, evidências e data. `DONE` exige código, teste e visual revisado quando aplicável. Itens dependentes do proprietário têm estado `BLOCKED` com causa.
3. Executar `python3 scripts/update_progress.py --end-run --summary "..." --tests "..."`.
4. Abrir e validar `progress/checklist.html`; confirmar totais por frente.
5. Incluir no resumo final: IDs, arquivos, testes, screenshots, risco de regressão, plano seguinte.
6. Atualizar o `CLAUDE.md`/`AGENTS.md` existentes adicionando referência a este documento e reforço da obrigação; preservar outros contratos já presentes.
7. Hook de Stop é apenas salvaguarda: não serve para autorizar status DONE nem substituir fechamento explícito.
8. Se o run só alterou arte e não executou um teste, registrar `IN_PROGRESS` com screenshot, não `DONE`.

---

## 12. Testes de aceitação e quality gates

### Gate A — funcionalidade desktop
- Abre como aplicativo instalado, fora do navegador; atalhos, menus, tema e janelas funcionam.
- Login e logout corretos; sessão remota; recuperação de conexão; bloquear live offline.
- Versão e modo DEMO/LIVE visíveis; backend continua após fechar UI.
- Instalação/atualização/desinstalação testadas nas plataformas suportadas.

### Gate B — clareza da interface
- Em sessão observada de teste, usuário identifica: receita/resultado, aprovações, agente ativo e abrir missão em até 10s (meta de UX com medição).
- Tarefa típica `abrir agente → encontrar tarefa atual → ver resultado` sem busca textual escondida, no máximo 3 interações primárias (meta).
- Tela 1366×768 utilizável sem sobreposição/corte de botões ou scroll horizontal inesperado.
- Navegação por teclado; foco visível, sem modal sem saída; contraste e labels adequados; `prefers-reduced-motion` respeitado.
- Todas as telas possuem loading, empty, error, disconnected e conteúdo longo tratados.

### Gate C — qualidade visual 3D
- Diorama tem cenário original coerente; não há geometria de placeholder como entrega final.
- Seis ambientes legíveis, personagens destacados, materiais, sombras e câmera polidos.
- Clique em avatar abre dados corretos; 3D não impede operação 2D.
- Animações acompanham eventos reais; no modo DEMO tudo rotulado.
- Capturas 3D (geral, sala, avatar selecionado), medição de FPS e teste no webview alvo anexados.
- Revisão visual humana: não marcar aprovado até validação de qualidade pelo proprietário.

### Gate D — regressão do negócio
- Mesmos endpoints/contratos (salvo mudanças versionadas), autorização e ledger inalterados.
- Aprovar orçamento exige backend, auditoria e estado persistente.
- Marketplace gates permanecem bloqueados quando não homologados.
- Não houve perda de dados por migração. Unit/integration/e2e existentes passam, ou falhas pré-existentes documentadas e reproduzidas.

### Cenários E2E mandatórios
1. Abrir app → fazer login → Centro de Comando mostra modo/conexão.
2. Abrir Diretório → encontrar Luna → ver tarefa real → navegar ao workflow → voltar.
3. Iniciar missão em DRY_RUN → mudança aparece simultaneamente na lista e no escritório.
4. Selecionar agente 3D → drawer → chat/tarefa → voltar sem perder câmera.
5. Criar aprovação simulada → inbox → aprovar → confirmar evento persistido e visual.
6. Desconectar rede → aviso/cached stale → bloqueio de mutação → reconectar/reconciliar.
7. Abrir cena em qualidade baixa → executar 30–60 segundos → monitorar FPS/memória.
8. Minimizar / restaurar / fechar → serviços da VPS continuam, tela reidrata.
9. Testar navegação e tabulação a 1366×768 e 1920×1080.
10. Confirmar que tarefas existentes e históricos não foram apagados pela troca de renderer.

### Evidências
- `docs/desktop-migration/screenshots/before/` e `/after/`.
- `docs/desktop-migration/visual-review.md` com checklist de defeitos, resolução e aprovação.
- `docs/desktop-migration/performance-report.md` com plataforma, SO, GPU, resolução, FPS, draw calls, memória e qualidade.
- `docs/desktop-migration/packaging-report.md` com comandos, outputs, assinatura/distribuição e limitações.

---

## 13. Backlog em ordem sugerida e dependências

**Trilha Desktop:** audit → ADR → runtime → auth → AppShell → empacotamento → tray/updater.

**Trilha 2D:** inventário → design system → Command Center → agentes/workflow → aprovações → restante dos domínios.

**Trilha 3D:** art bible → vertical slice polida → states/events → salas → avatares → perf/reconexão → revisão visual.

**Não serializar as trilhas:** depois do audit, construir desktop/2D/3D de forma paralela com contratos congelados. Uma fatia de 3D deve poder ser vista no aplicativo desde cedo; a expansão do 3D fica condicionada à qualidade do vertical slice.

### Ordem de implementação do primeiro incremento
1. Audit + screenshots before.
2. Escolher e justificar Tauri com teste mínimo de renderização React/3D.
3. Criar app shell desktop + conexão API + status.
4. Criar design tokens e diretório novo de agentes.
5. Produzir 1 ambiente 3D completo e 2 personagens acabados.
6. Conectar clique → drawer real e status WebSocket.
7. Executar testes + screenshots after + checklist no mesmo run.

---

## 14. Critérios finais de conclusão

**`DESKTOP_UX_READY = true` somente quando:**
- executável desktop empacotado e testado;
- todas telas administrativas essenciais migradas e com paridade funcional;
- gestão dos 13 agentes legível e consistente;
- testes de UX, acessibilidade e E2E críticos passam;
- visual validado com screenshots e revisão humana;
- backend/mocks/políticas do SDD v2 preservados;
- checklist reconciliado e histórico atualizado.

**`OFFICE_3D_READY = true` somente quando:**
- a cena tem acabamento visual original, salas e avatares previstos, ações clicáveis;
- estados reais (ou DEMO indicado) sincronizados sem duplicar lógica;
- existe fallback 2D completo e FPS medido em hardware-alvo;
- revisão visual humana realizada.

**`LIVE_READY` continua regido pelos gates fiscais/comerciais do SDD v2**, não por estes dois indicadores.

---

## 15. Instruções de entrega e comunicação do Claude Code

A cada run, reportar:
- **Mudanças reais:** arquivos, componentes, endpoints alterados, motivo.
- **Antes/depois:** screenshots e observações verificáveis.
- **Testes:** nome, comando e resultado, incluindo falhas.
- **Qualidade visual:** situação `DRAFT / REVIEW / ACCEPTED` por tela/cena; não usar 'foda' como critério técnico.
- **Compatibilidade:** Windows/macOS testado ou não testado, sem inventar.
- **Checklist:** IDs atualizados e comando executado.
- **Bloqueios:** credenciais/contas/hardware/assinatura não disponíveis.
- **Próxima fatia:** funcionalidade concreta e seus critérios.

Se o código existente não estiver acessível, não alegar que a migração foi realizada. Se não puder produzir binário por limitações de SO/runner, implementar o código, produzir testes possíveis e marcar entrega do instalador como bloqueada até build verificável.

---

## 16. Fonte de verdade e prioridade documental

1. **Segurança, autorização financeira, fiscal, homologação e contratos externos:** SDD v2.0 e código validado da API.
2. **Experiência desktop, front administrativo, visual dos agentes e 3D:** este SDD aditivo v3.0-DESKTOP.
3. **Situação real de implementação:** repositório auditado, testes e `progress/tasks.json`, nunca texto assumido do SDD.
4. **Divergência:** registrar ADR, compatibilizar contratos, preservar dados; não fazer migração destrutiva silenciosa.

**Resultado esperado:** AI Commerce Desktop com experiência de comando clara, avatares e escritório 3D memoráveis, mantendo por trás o mesmo motor de comércio e agentes inteligentes já planejado.
