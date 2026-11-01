# AI Commerce Desktop — Como aplicar

1. Copie `SDD_Desktop_UX_3D_v3.md` para `docs/SDD_Desktop_UX_3D_v3.md` no repositório em andamento.
2. **Não substitua** o SDD v2.0 existente. `SDD_v2_Referência.md` está neste pacote apenas como referência complementar.
3. Abra o Claude Code na raiz do projeto, envie `PROMPT_CLAUDE_CODE.md` e permita que ele audite a implementação existente.
4. O Claude deverá atualizar, sem duplicar, `CLAUDE.md`, `AGENTS.md`, `progress/tasks.json` e `progress/checklist.html`, usando o script que já existe.
5. Não marque tarefas de desktop, visual ou 3D como concluídas até haver build/teste/screenshot e revisão de qualidade.
6. O app administrativo será desktop; a loja do consumidor continua web (WooCommerce/outro marketplace). O backend dos agentes permanece na VPS.

Este pacote contém especificação e prompt, **não** contém um aplicativo já implementado.
