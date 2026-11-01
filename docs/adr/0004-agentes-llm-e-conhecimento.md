# ADR 0004 — Agentes, LLM e Knowledge Hub

**Data:** 2026-10-24 · **Estado:** aceito · **Contexto:** SDD §7, §11, §12; FR-002/003/004/017/018; AI-001..010

## Decisões
1. **Provedor:** `LLMProvider` com `MockLLMProvider` (padrão: determinístico, sem rede/custo) e
   `AnthropicLLMProvider` (Claude API, SDK `anthropic`), ativado por `AIC_LLM_PROVIDER=anthropic`.
   Toda saída usa `output_config.format` (JSON Schema estrito) e é validada por código antes de uso.
   No `claude-opus-5-5`: `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) para recusas.
   `stop_reason` `refusal`/`max_tokens` viram erros tipados (nunca saída parcial).
2. **Roteamento (SDD §12):** modelo pequeno por padrão (`claude-haiku-5-5`); escalona para `claude-opus-5-5` em
   tarefa complexa (Atlas/Clara), risco HIGH/CRITICAL ou após saída inválida. Ambos configuráveis por ambiente.
3. **Custo:** `ai_usage` append-only em micro-dólares por agente/tarefa/missão/modelo; orçamento mensal
   (`AIC_AI_MONTHLY_BUDGET_MICRO_USD`) e limites por execução vindos de `agent_configs.limits`
   (`stop_reason=LIMIT_REACHED`). Cache de respostas por hash de conteúdo com TTL; system prompt estável e
   marcado com `cache_control` para prompt caching.
4. **Workflow durável:** motor próprio sobre a máquina de estados de `agent_tasks` + `workflow_checkpoints`
   (append-only). **LangGraph não foi adotado nesta fase**: o grafo atual é simples (plano → subtarefas → resumo)
   e o motor próprio evita dependência e um segundo armazenamento de checkpoints. Reavaliar se os grafos
   ganharem ramificações/condicionais complexas.
5. **Agentes não agem:** cada especificação monta fatos via SQL/código, o LLM interpreta, validadores
   rejeitam citação/número inventado, promessa proibida e reembolso prometido; efeitos externos só como
   ActionProposal (ex.: plano do Pixel → CREATE_CAMPAIGN pendente). Clara sempre escalona fiscal.
6. **Knowledge Hub:** FTS português + pgvector (HNSW) com fusão RRF; ACL por papel/agente; validade;
   normas e conteúdo com indício de prompt injection exigem revisão humana antes de serem recuperáveis;
   auditoria de cada recuperação. Embeddings atuais são **léxicos** (feature hashing) — trocar por um
   provedor de embeddings semântico quando contratado (interface pronta).
7. **Configuração versionada:** o seed grava nova versão de `agent_configs` quando a definição no código muda.

## Pendências
- Medir qualidade/custo com o modelo real (`python -m aicommerce.ai.evals` com provedor anthropic) antes de
  operar; resultados atuais são do mock.
- Embeddings semânticos; chat com streaming.
