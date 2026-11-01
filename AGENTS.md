# AI Freelance Agents — Regras obrigatórias do agente de desenvolvimento

Leia `docs/plano/PLANO_FREELANCE_COM_AGENTES.md` antes de alterar a arquitetura. A documentação do AI Commerce em `docs/legado/` é só referência histórica.

## Segurança (repositório PÚBLICO)
- Nunca versionar segredos, chaves de API, tokens, IPs ou endereços de servidores, nem dados pessoais. Credenciais ficam em `infra/env/*.env` (ignorado).
- Antes de cada push: `gitleaks dir . --redact` sem achados reais.
- Toda ação com efeito externo ou gasto passa por proposta e aprovação humana (`governance.propose`).
- Antes de usar qualquer plataforma de terceiros, ler e citar as regras oficiais dela.

## Checklist após TODA run
1. Atualize somente as tarefas de `progress/tasks.json` realmente modificadas.
2. Status permitidos: NOT_STARTED, IN_PROGRESS, IN_REVIEW, BLOCKED, DONE. `DONE` exige evidência real em `evidence`.
3. Execute `python3 scripts/update_progress.py --end-run --summary "resumo" --tests "comando e resultado"` antes de terminar.
4. Verifique `progress/checklist.html` e registre IDs alterados, testes, bloqueios e próximos passos na resposta.
