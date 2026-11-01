# ADR 0005 — Sala de cada agente no escritório 3D segue o cadastro do backend

**Status:** aceito (24/10/2026) · resolve o achado A14 da auditoria `docs/audits/codex-2026-10-24`.

## Contexto
O SDD v3 §7 lista 7 ambientes com exemplos de ocupantes (ex.: "Research Lab: pesquisas de Luna e Orion";
"Operations & Support: Logan/Eva"; "Marketing Studio: Iris/Pixel/Vera"). O cadastro de agentes do backend
(`apps/api/src/aicommerce/workforce/registry.py`, vindo do SDD v2) define a sala de trabalho de cada agente por
departamento operacional: Vera em `research` (qualidade e conversão de anúncios), Orion em `finance` (analytics),
Logan em `store` (logística da loja) e Dante em `meeting` (modelagem comercial com o Atlas).

O SDD v3 também diz: "Departamentos podem compartilhar salas e personagens podem mover-se entre elas por eventos
de tarefa" e "a cena nunca inventa estado: segue `agent_presence`".

## Decisão
1. A posição de cada agente na cena é **sempre** a sala informada pela presença do backend (`room`), mapeada para
   um dos 7 ambientes por `apps/web/lib/officeHq.ts` (`zoneOfRoom`). O frontend não tem uma segunda tabela de
   lotação: se o backend mudar a sala de um agente, ele aparece no novo ambiente (teste E2E 4c).
2. Os exemplos de ocupantes do SDD v3 §7 são lidos como **ilustrativos**. Prevalece o cadastro do backend (SDD v2
   é a fonte de verdade de negócio; o v3 é aditivo para desktop/UX/3D — SDD v3 §16).
3. A Sala de Reuniões hospeda o Dante (modelagem comercial) e serve de ponto de encontro; "reuniões visuais"
   continuam proibidas sem evento real.

## Consequências
- Para mudar a lotação (ex.: levar o Orion para o Laboratório), basta alterar `room` no cadastro do backend e
  rodar o seed; a cena acompanha sem mudança de código. Os móveis de cada sala têm lugares de sobra
  (`extra` em `officeHq.ts`) para agentes adicionais.
- Revisão visual humana decide se a lotação atual comunica bem os departamentos (pendente, UXD-017/018).
