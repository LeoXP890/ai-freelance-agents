# ADR 0007 — Desvios arquiteturais aceitos pelo proprietário e revisão da autenticação desktop

**Data:** 25/10/2026 · **Status:** aceita (decisão do proprietário no aceite de 25/10 —
[`ACEITE_PROPRIETARIO_2026-10-25.md`](../ACEITE_PROPRIETARIO_2026-10-25.md)).

## 1. Orquestração: motor próprio em vez de LangGraph (SDD v2)
- **Decisão:** manter o motor próprio (máquina de estados de `agent_tasks` + `workflow_checkpoints` no PostgreSQL),
  já registrado na [ADR 0004](0004-agentes-llm-e-conhecimento.md) §4. **Não migrar** para LangGraph só para seguir
  a letra da SDD.
- **Requisitos funcionais da SDD que continuam atendidos (com teste):** persistência de cada passo
  (`workflow_checkpoints`), retomada após queda do processo em pontos reais de falha (`test_crash_recovery.py`, via
  `AIC_FAULT_POINT`), cancelamento sem efeito tardio (`test_cancellation.py`), limite de fan-out e de orçamento por
  missão (`test_ai_budget_*`), governança (nenhum passo executa ação externa: vira proposta com MFA).

## 2. Escritório 3D: geometria procedural em React Three Fiber em vez de pipeline Blender/GLB (SDD v3)
- **Decisão:** manter a geometria procedural otimizada (estático "assado" em poucas malhas — `office/Baked.tsx`;
  personagens paramétricos — `office/Character.tsx`, `lib/poses.ts`). O proprietário aprovou o resultado artístico.
  **Não converter** personagens ou salas para Blender sem necessidade técnica.
- **Preservado:** o caminho de exportação/substituição de assets — `apps/web/scripts/build_office_assets.mjs`
  (GLTFExporter → `public/assets/office/*.glb` + `manifest.json` com bytes/triângulos). Um asset feito no Blender
  pode substituir uma peça mantendo o nome no manifesto, se um dia for preciso.
- **Orçamento de desempenho mantido em teste** (draw calls/triângulos nas 4 qualidades; 60 FPS medidos no Mac).

## 3. Autenticação desktop: autenticação própria (senha + TOTP) em vez de OAuth/OIDC PKCE (SDD v3 §128/§468)
**Decisão:** manter. O app tem um único dono/servidor (sem federação nem login social), e um provedor de identidade
externo traria custo e dependência sem ganho de segurança proporcional nesta fase. Não implementar outro IdP sem
justificativa.

**Revisão de segurança feita (25/10/2026) — sem vulnerabilidade material encontrada:**
| Item da SDD v3 | Implementação | Equivalência / limitação |
|---|---|---|
| Tokens no cofre do SO | Credencial de "computador lembrado" no Keychain/Credential Manager (comandos nativos fora da thread da interface) | equivalente ao refresh token protegido pelo SO |
| Token de acesso | JWT de **15 min** em `sessionStorage` (some ao fechar o app), enviado só no cabeçalho `Authorization` e na 1ª mensagem do WebSocket — nunca em URL | **limitação:** qualquer script que rode na página leria o token; mitigado por CSP `script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, nenhum HTML injetado no código, validade curta |
| Revogação | claim `sv` (versão de sessão) conferida em toda requisição HTTP **e** periodicamente no WebSocket; troca de senha/MFA encerra sessões (`test_password.py`) | equivalente a revogar refresh tokens |
| MFA | TOTP + códigos de recuperação; decisões de aprovação, gates, perfil e elegibilidade exigem sessão com MFA | atende |
| Logout | limpa token da sessão e permite revogar o computador lembrado | atende |
| PKCE/navegador do sistema | não aplicável sem IdP externo; o OAuth com PKCE existe onde há terceiro (conta do Mercado Livre, feito no servidor) | desvio aceito |
**Limitações registradas (não são vulnerabilidades reproduzíveis):** `style-src 'unsafe-inline'` (só estilos);
`connect-src` aceita `https:`/`wss:` porque o endereço do servidor é configurável — se houvesse injeção de script,
ela poderia enviar dados a outro host; a defesa é não permitir script de fora (`script-src 'self'`).
Reavaliar OIDC se houver vários usuários externos, SSO corporativo ou exigência regulatória.
