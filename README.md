# AI Freelance Agents

Agentes de IA que analisam projetos do Workana e preparam perguntas e propostas personalizadas, com conferência automática das regras da plataforma. O envio é sempre feito pelo dono.

- **Plano de transformação:** [`docs/plano/PLANO_FREELANCE_COM_AGENTES.md`](docs/plano/PLANO_FREELANCE_COM_AGENTES.md)
- **Base:** cópia do AI Commerce (time de 13 agentes de IA com aprovações humanas, teto de custo, auditoria, app desktop e escritório 3D), feita em 01/11/2026 sem histórico, sem segredos e sem dados do piloto. A documentação original está em `docs/legado/`.
- **Estado:** o código ainda é o do AI Commerce; a transformação segue as fases do plano.

## Rodar em modo simulado (DRY_RUN)

```bash
make up        # cria infra/env/dry_run.env e sobe a stack em http://localhost:8080
make test      # testes da API
```

Credenciais reais nunca entram no repositório: ficam em `infra/env/*.env` (ignorado pelo git).
