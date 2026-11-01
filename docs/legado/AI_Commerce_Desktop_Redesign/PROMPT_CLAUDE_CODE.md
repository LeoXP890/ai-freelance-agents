# Prompt para iniciar a migração do AI Commerce no Claude Code

Leia integralmente `docs/SDD_Desktop_UX_3D_v3.md` e o SDD v2 que já existe no repositório, além de `CLAUDE.md`, `AGENTS.md` e `progress/tasks.json`.

Sua missão é transformar a interface administrativa atual do AI Commerce em um **aplicativo desktop instalável, profissional, intuitivo e visualmente bem-acabado**, preservando o backend, agentes, banco, integrações, aprovações e execução na VPS. A interface atual não foi aprovada visualmente; não se limite a mudar cores. Refatore navegação, dashboard, visualização dos 13 agentes e fluxo de trabalho e produza um escritório 3D isométrico estilizado, divertido e com qualidade de jogo de gerenciamento.

**Regras imediatas:**
1. Faça auditoria do repositório real. Capture screenshots BEFORE e descreva problemas concretos. Não presuma que telas ou funcionalidades existem porque estão no SDD.
2. Crie ADR justificando Tauri 2, compatibilidade do frontend atual com exportação estática/Vite e teste real WebGL em ambiente-alvo. Escolha Electron somente se justificado por evidências.
3. Preserve código funcional, contratos, dados e lógica de negócio. Migre por fatias e deixe rollback possível.
4. Entregue o shell desktop utilizável junto com um novo diretório de agentes e um vertical slice 3D **bem acabado** (uma sala, dois avatares) antes de expandir a cena. Sem placeholders como 'concluído'.
5. Todos os estados exibidos devem ser reais ou DEMO visivelmente sinalizado. As operações que gastam dinheiro permanecem sujeitas ao backend de governança.
6. Execute testes e compare screenshots antes/depois. Se não puder fazer revisão visual na máquina-alvo, relate isso e deixe como pendência.
7. Ao final **de todo run**, atualize `progress/tasks.json`, execute `python3 scripts/update_progress.py --end-run --summary "..." --tests "..."`, valide `progress/checklist.html` e inclua IDs e evidências no resumo.
8. Continue implementando a frente 2D e 3D em paralelo até que os quality gates do SDD estejam satisfeitos; não encerre trabalho apenas com scaffolding.

Comece agora pela auditoria e pelo primeiro incremento vertical executável, usando o que existe no repositório. Se o SDD ou scripts do checklist não estiverem no local indicado, localize os equivalentes existentes antes de criar duplicatas.
