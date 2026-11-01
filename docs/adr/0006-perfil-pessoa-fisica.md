# ADR 0006 — Perfil do vendedor Pessoa Física (CPF) e elegibilidade por canal

**Data:** 25/10/2026 · **Status:** aceita · **Substitui em parte:** a premissa de "operação empresarial" do SDD v2 §3
(gate `LEG-01` = CNPJ/CNAE/regime/IE). Decisões anteriores continuam no histórico; ver o adendo A1 do SDD v2.

## Contexto
O proprietário não tem CNPJ e não pretende abrir CNPJ ou MEI; quer operar com CPF, dentro do que a lei e os canais
permitirem. O desenho original presumia pessoa jurídica: `LEG-01` pedia CNPJ, CNAE, regime e IE e era obrigatório para
toda venda real; textos de homologação e de pendências pediam "abra o CNPJ".

## Decisão
1. **Perfil do vendedor persistente e tipado** (`seller_profile`, uma linha): `PERSON` (Pessoa Física) ou `COMPANY`
   (Pessoa Jurídica, mantido para compatibilidade). Padrão: `PERSON`. País, UF, modelo fiscal pretendido,
   restrições, data/origem da verificação, versão. **O CPF/CNPJ não é armazenado** (não é necessário hoje); só o estado
   `tax_id_status = NOT_COLLECTED`. Se um dia for preciso guardar, exigirá: validação, criptografia em repouso (mesmo
   chaveiro do PII), máscara na interface, RBAC, proibição em log, retenção mínima e auditoria de acesso.
2. **Matriz de elegibilidade por canal e perfil** (`channel_eligibility`): seis aspectos avaliados separadamente —
   cadastro, venda habitual, acesso à API, venda à ordem, envio e obrigações fiscais — cada um `SUPPORTED`,
   `CONDITIONAL`, `UNVERIFIED` ou `UNSUPPORTED`, com fonte, data, versão de política, escopo, restrições e pendências.
   O estado do canal é o **pior** dos aspectos. Estado inicial: **todos `UNVERIFIED`** (as fontes encontradas são
   secundárias e divergentes; nada foi comprovado).
3. **GoLivePolicy recebe o perfil como parâmetro obrigatório** (sem valor padrão). A lista de gates exigidos não muda
   e nunca fica vazia (`LEG-01` continua obrigatório, com o mesmo código). Para venda real passam a valer, além dos
   gates:
   - aprovação de gate dependente de perfil (`LEG-01`, `TAX-01/02/03`, `CH-*`) vale **só para o perfil em que foi
     dada** (`scope.seller_types`, gravado pelo sistema — o usuário não pode informar); aprovações antigas sem perfil
     valem apenas para PJ (desenho original);
   - em PF, `LEG-01` e `TAX-*` só podem ser aprovados com **parecer profissional** (evidência `PARECER`) anexado;
   - em PF, o canal precisa de elegibilidade comprovada (`SUPPORTED`/`CONDITIONAL`) em **cada aspecto exigido** pelo
     modelo (venda à ordem só conta quando o envio é pelo fornecedor). PJ sem linha na matriz segue o desenho
     original (o gate do canal comprova a conta apta); PJ com linha é avaliada igual.
   Bloqueios em PF dizem "Operação com Pessoa Física ainda não homologada para este modelo de venda." — nunca
   "CNPJ obrigatório" sem confirmação para o escopo.
4. **Revisão obrigatória quando a base muda:** trocar o perfil derruba (estado `EXPIRED`, auditado, evento
   `go_live.revoked`) todas as aprovações dependentes de perfil; registrar nova elegibilidade de um canal derruba a
   aprovação do gate daquele canal.
5. **Só o proprietário com MFA** muda perfil ou elegibilidade (`gates:approve`). Comprovar um aspecto exige nota e
   fonte `https`; condicional exige restrições; é preciso informar os seis aspectos. Operador e agentes não podem.
6. **Textos por perfil:** os registros históricos dos gates no banco não mudam; a API apresenta o texto do perfil em
   vigor (ex.: `LEG-01` em PF = "Elegibilidade da Pessoa Física para o modelo de venda").
7. **Agentes:** Clara, Dante, Nova, Atlas e Finn recebem o perfil e a matriz como fatos; validadores recusam Nova
   preparando canal não comprovado fora da simulação, Dante recomendando canal não comprovado sem declarar "só
   simulação", e Clara dispensando validação humana em pergunta sobre CPF/Pessoa Física (red-team 19/19).

## Consequências
- DRY_RUN funciona igual com qualquer perfil (nada aqui é consultado para simular).
- Nenhuma combinação de perfil, elegibilidade ou endpoint transforma "não verificado" em autorização: venda real
  exige matriz comprovada **e** todos os gates aprovados para o perfil, com validade, hash e MFA.
- Regra proibida e testada: não existe `PERSON → ALLOW`; `required_gates()` nunca retorna lista vazia.
- Testes: `tests/test_seller_profile.py`, `tests/test_golive.py` (PJ explícito), `tests/test_gates.py` (ciclo PJ),
  `tests/test_migrations.py` (0021 preserva registros), red-team em `ai/evals.py`, E2E "PF/CPF" no Playwright.

## Adendo (25/10/2026, encerramento) — condições verificáveis e invalidação por mudança material
1. **CONDITIONAL não é autorização irrestrita.** A elegibilidade guarda `conditions` estruturadas
   (`ufs`, `fulfillment_modes`, `supplier_origins`, `product_ids`, `supplier_ids`), conferidas contra a operação
   concreta em toda avaliação. Condição violada → DENY; condição que a operação não permite comprovar (ex.: falta o
   produto) → DENY; condição desconhecida/malformada → DENY; aspecto CONDITIONAL sem condição → recusado no cadastro e
   DENY na avaliação. As condições valem também em SUPPORTED, como escopo. A mesma avaliação roda na revalidação
   imediatamente antes de executar (`executor._preflight → approvals.reevaluate`), e os motivos detalhados entram na
   auditoria da falha. Migração `0022`.
2. **Mudança material no perfil derruba só o que depende dela:** UF → `LEG-01`, `TAX-01`, `TAX-03`; modelo fiscal →
   `LEG-01`, `TAX-01/02/03`; restrições → `LEG-01`; tipo PF/PJ → todos os dependentes de perfil. Mudança igual ou
   irrelevante não invalida. A invalidação é auditada (`seller.profile.updated` com `changed`/`gates_lost`,
   `gate.revoked` com o motivo, evento `go_live.revoked`).
3. **Permissão obsoleta não é reutilizada:** cada aprovação guarda `scope.profile_fingerprint` (hash das propriedades
   materiais daquele gate e, nos gates de canal, da versão/estado/condições da elegibilidade), gravado pelo sistema.
   Se o perfil mudar por qualquer caminho — inclusive fora da API — a impressão não bate e a avaliação nega.
Testes: `tests/test_governance_conditions.py`.

