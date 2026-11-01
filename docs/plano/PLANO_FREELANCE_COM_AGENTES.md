# Plano — AI Freelance Agents (propostas no Workana com agentes de IA)

**Origem:** cópia do AI Commerce feita em 01/11/2026 (sem histórico, sem segredos e sem documentos do piloto).
O AI Commerce continua preservado no repositório privado original.

## 1. Objetivo

Usar os agentes para conseguir trabalhos de **IA e automação no Workana**, perfil sem avaliações, com propostas
melhores e mais rápidas, **100% dentro das regras do Workana**. O dono escolhe os projetos e envia as propostas; os
agentes fazem a análise, a redação e a conferência de regras.

**Decisões do dono (01/11/2026):** o perfil do Workana **não será alterado**; o foco é **trabalho com IA**.

## 2. Regras do Workana que definem o desenho

Lidas em 01/11/2026 nas [Políticas da Workana](https://help.workana.com/hc/pt/articles/360040841214) e nos
[Termos e Condições](https://help.workana.com/hc/pt/articles/360041499974).

| Regra (fonte) | Consequência no sistema |
|---|---|
| Proibido robô, aranha ou processo automático para minerar, rastrear, extrair ou indexar o site (Termos) | **Nenhuma varredura, monitoramento ou envio automático.** O dono navega e envia. |
| Proibido compartilhar contato ou **qualquer link externo**, inclusive GitHub e Behance (Políticas §1 e §4) | O verificador bloqueia URL, domínio, e-mail, telefone e @usuário no texto e nos anexos. |
| Proibido oferecer serviço grátis ou trocar serviço por avaliação (Políticas §4 e §4.1) | Nada de amostra feita para o cliente, demonstração grátis ou "faço barato pela avaliação". |
| Proibido "propostas-teste" e preço injustificadamente baixo (§4.1) | Preço calculado pelo escopo; o verificador alerta preço abaixo do piso. |
| Proibido proposta genérica ou repetida (§4.1) | Similaridade com propostas anteriores acima do limite bloqueia o envio. |
| Proibido citar a comissão da plataforma (§4) | O verificador bloqueia menções à comissão. |
| Permitido e recomendado: mandar mensagem com perguntas antes da proposta final (§4.1) | Os agentes sugerem perguntas de esclarecimento. |
| Permitido anexar amostras dos **seus** trabalhos ("Quais informações devo incluir em uma proposta?") | PDF de portfólio com trabalhos do dono, **sem links nem contatos**. |
| Responder ao cliente em até 24 ou 48 horas ("Razões para suspensão") | Lembrete no app para conversas sem resposta. |

Pontos que **não** estão claros nas regras e precisam de confirmação no suporte do Workana antes de usar:
- se o botão de captura (seção 4) conta como "extração de informação", mesmo lendo só a página aberta pelo dono,
  com um clique dele. Até haver confirmação, o modo padrão é **colar o texto**.

## 3. Fluxo

```
Dono abre a busca salva no Workana ─► escolhe um projeto ─► cola o texto no app (ou usa o botão, se liberado)
        │
        ▼
Luna: entende o pedido, o cliente e a concorrência visível (número de propostas)
Dante: escopo, limites, etapas com pagamento por etapa, prazo e preço
Iris: redige 2 ou 3 perguntas de esclarecimento + a proposta, no estilo do dono
Clara: confere as regras (seção 2) e explica cada bloqueio
Vera: revisa clareza e personalização
        │
        ▼
Dono revisa, copia e envia no Workana ─► registra o resultado no app (enviada, respondida, contratada, recusada)
        │
        ▼
Orion: mede taxa de resposta e de contratação por tipo de projeto e ajusta a triagem
Finn: registra a receita dos projetos ganhos
```

## 4. Componentes

**Backend (reaproveita o núcleo do AI Commerce):**
- Modelo `freelance_leads`: texto do projeto colado pelo dono, categoria, orçamento, número de propostas no momento,
  nota de triagem e motivos.
- Modelo `freelance_proposals`: perguntas, proposta, preço, prazo, etapas, versão, resultado da conferência de regras, status.
- Modelo `freelance_outcomes`: resultado informado pelo dono e datas.
- Tarefas de agente: `freelance.triage`, `freelance.draft`, `freelance.rules_check`, `freelance.review` (usam o
  runtime, os limites e o teto de custo que já existem).
- **Verificador de regras** (`freelance/rules.py`), em código e testado, antes de qualquer LLM:
  URLs e domínios, e-mail, telefone, @usuário, palavras de gratuidade, menção à comissão, preço abaixo do piso e
  similaridade com propostas anteriores. O LLM (Clara) só complementa, nunca libera o que o código bloqueou.
- **Estilo do dono:** algumas propostas antigas, que o próprio dono colar no app, servem de exemplo de tom.

**Interface (app desktop):**
- Tela **Projetos**: colar texto → nota de triagem com motivos (concorrência, orçamento, clareza, encaixe).
- Tela **Proposta**: perguntas, proposta, preço e prazo, com o resultado da conferência de regras e um botão "copiar".
- Tela **Resultados**: funil (enviadas → respondidas → contratadas) e o que mais funciona.

**Portfólio em PDF (anexo permitido):** gerado a partir de trabalhos do próprio dono (por exemplo, o AI Commerce),
com prints e diagrama, **sem links, e-mail, telefone ou nomes de usuário**. O verificador também analisa o PDF.

**Botão de captura (opcional, só se o Workana confirmar que é permitido):** extensão do Chrome que, com um clique do
dono, lê **apenas** a página do projeto aberta e envia ao app local. Não navega, não abre listas, não roda sozinha.

## 5. Agentes usados

Atlas (coordena), Luna (análise do projeto), Dante (escopo e preço), Iris (redação), Clara (regras),
Vera (revisão), Orion (métricas) e Finn (receita). Os demais agentes ficam desligados neste repositório.
Os módulos de e-commerce ficam no código como legado, desligados.

## 6. Triagem (para não gastar conexões à toa)

Pontuação de 0 a 100 com motivos explícitos:
- **Concorrência:** mais de 20 propostas reduz muito a nota (em 01/11/2026, a mediana em "agente de IA" era 49).
- **Clareza:** pedido vago ("SaaS completo") reduz a nota.
- **Orçamento × escopo:** orçamento incompatível reduz a nota.
- **Encaixe:** projetos de IA aplicada a um negócio concreto (atendimento, qualificação de leads, consulta a
  documentos, automações) aumentam a nota.
- **Prazo:** prazo que o dono não consegue cumprir, considerando o emprego dele, zera a nota (atraso gera suspensão).

## 7. Fases

| Fase | Entrega | Critério de aceite |
|---|---|---|
| **F0. Fork limpo** | Repositório público sem segredos, CI verde | `make test` verde; gitleaks sem achados reais |
| **F1. Verificador de regras** | `freelance/rules.py` com testes para cada regra da seção 2 | Cada regra tem teste de bloqueio e de liberação; nenhum texto com link passa |
| **F2. Triagem e proposta** | Modelos, tarefas de agente e API; geração de perguntas e proposta | Teste ponta a ponta com 5 projetos reais colados; tempo menor que 60 s; custo de IA registrado |
| **F3. Interface** | Telas Projetos, Proposta e Resultados no desktop | Build do desktop; screenshots; revisão do dono |
| **F4. Portfólio em PDF** | Gerador de PDF a partir de trabalhos do dono | PDF sem links nem contatos, confirmado pelo verificador |
| **F5. Medição** | Funil e aprendizado da triagem | Relatório após 30 propostas |
| **F6. Botão de captura** | Extensão do Chrome | **Só depois de confirmação escrita do Workana** |

## 8. Experimento e regra de parada

- **Meta:** 30 propostas enviadas com o sistema.
- **Métricas:** taxa de resposta do cliente (mensagem ou pergunta de volta) e contratações.
- **Regra:** se 30 propostas não gerarem nenhuma resposta, a estratégia é revista (categoria, preço, formato) antes de continuar.

## 9. Fora de escopo, por regra ou por decisão

- Enviar propostas automaticamente, monitorar ou varrer o Workana.
- Links, demonstrações, repositórios ou contatos em propostas e anexos.
- Amostras do trabalho do cliente, trabalho grátis ou desconto em troca de avaliação.
- Alterar o perfil do dono.

## 10. Decisões do dono

1. Confirmar com o suporte do Workana se o botão de captura é permitido (fase F6).
2. Piso de preço por hora e por projeto.
3. Quantas horas por semana estão disponíveis para executar projetos (isso entra na triagem de prazo).
