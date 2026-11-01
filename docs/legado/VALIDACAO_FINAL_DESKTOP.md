# Validação final do app desktop — roteiro do proprietário

**Versão:** a indicada em [`ENTREGA_DESENVOLVIMENTO.md`](ENTREGA_DESENVOLVIMENTO.md) §7 (finalização do desenvolvimento +
perfil Pessoa Física) · **App:** AI Commerce 0.1.0 (macOS: `.dmg` em `dist/desktop/0.1.0/`; Windows: artefato
`desktop-windows` do GitHub Actions).

> **Situação em 25/10/2026:** o proprietário **aprovou formalmente** o produto para a fase DRY_RUN
> ([`ACEITE_PROPRIETARIO_2026-10-25.md`](ACEITE_PROPRIETARIO_2026-10-25.md)), com base nas telas, capturas e
> documentos. As caixas abaixo não foram marcadas porque os passos físicos não foram executados por ele; o roteiro
> continua disponível para conferências futuras (o Windows físico, §8, foi dispensado pelo proprietário) e para pedidos de melhoria.

Como usar: faça cada passo no **app instalado** (não no navegador). Marque **Aprovado** ou descreva o ajuste e anote a
evidência (print, horário, computador). Quando me devolver, eu corrijo os ajustes e registro os aprovados no checklist
(é a sua aprovação que transforma "em revisão" em "concluída").

**Preparação:** sistema local no ar — `docker compose -f infra/compose/docker-compose.yml --env-file infra/env/dry_run.env up -d`
— e entre com `owner@demo.local` / `simulado-123` (o app pede para cadastrar o MFA no primeiro acesso). Tudo é
**simulação**: nada é vendido, gasto ou publicado.

Legenda: ☐ aprovado · Ajustes: o que mudar · Evidência: print/horário/computador.

## 1. Instalação, abertura e sessão
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 1.1 | Abrir o app pela primeira vez | Abre; macOS pode pedir "Permitir" ao Keychain (sem certificado Apple isso se repete a cada versão) | ☐ | | |
| 1.2 | Login + MFA | Código do app autenticador aceito; senha errada recusada com mensagem clara | ☐ | | |
| 1.3 | "Lembrar este computador" e reabrir o app | Entra sem pedir senha; em Configurações aparece o computador; "Revogar" pede login de novo | ☐ | | |
| 1.4 | Configurações → Servidor | Aceita `http://localhost…`; recusa `http://` de servidor remoto | ☐ | | |

## 2. Navegação e aparência
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 2.1 | Percorrer as 17 telas da barra lateral | Todas abrem sem erro; título e trilha corretos | ☐ | | |
| 2.2 | ⌘K (paleta) e atalhos ⌘1…⌘5 | Paleta busca telas/agentes; foco fica dentro dela; Esc fecha | ☐ | | |
| 2.3 | Tema Claro/Escuro/Sistema (Configurações) | Textos legíveis em todas as telas, inclusive Usuários e Auditoria | ☐ | | |
| 2.4 | Janela pequena (≈1100×700) e tela cheia | Sem rolagem horizontal; nada cortado | ☐ | | |
| 2.5 | Navegar só com teclado (Tab/Shift+Tab/Enter) | Foco sempre visível; diálogos prendem o foco | ☐ | | |
| 2.6 | Estados vazios e erros | Telas sem dados explicam o porquê; erro de servidor ≠ "nenhum item" | ☐ | | |
| 2.7 | Rolar Configurações e Financeiro até o fim com a roda do mouse | O fim da página aparece; barra lateral fica no lugar | ☐ | | |

## 3. Agentes, conversas e missões
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 3.1 | Agentes → abrir a Luna → pedir uma pesquisa | Tarefa aparece, conclui, mostra fontes **simuladas** e custo | ☐ | | |
| 3.2 | Conversas → perguntar ao Atlas | Resposta com fonte/rota; aviso "o agente não executa nada pelo chat" | ☐ | | |
| 3.3 | Missões → Nova missão | Etapas criadas (Luna/Nexus/Finn…), linha do tempo real, resumo no fim | ☐ | | |
| 3.4 | Cancelar uma missão em andamento | Para; nada novo é criado depois | ☐ | | |
| 3.5 | Agentes → abrir a Nova (com proposta pendente) | "Precisa de você" lista a proposta; clicar abre a Central já nela (sem aprovar nada); "O que sabe fazer" lista as tarefas | ☐ | | |
| 3.6 | Missões → Nova missão com 5 etapas | Atlas coordena Luna, Nexus, Finn, Clara e Nova; tudo marcado como simulação | ☐ | | |

## 4. Aprovações e dinheiro
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 4.0 | Aprovações → olhar a lista | Cada linha diz o quê (ex.: "Publicar anúncio · Garrafa…"), agente, canal, SKU, risco (baixo/médio/alto), quando e motivo | ☐ | | |
| 4.1 | Aprovações → abrir uma proposta | Dossiê: por que precisa de você, o que será executado, **o que muda**, evidências | ☐ | | |
| 4.2 | Aprovar com MFA | "Decisão registrada no servidor"; contador da barra lateral diminui | ☐ | | |
| 4.3 | Pedidos → cancelar um pedido pago | Aparece proposta de reembolso para aprovar; alerta crítico até estornar | ☐ | | |
| 4.4 | Financeiro → "Como os números são calculados" | Bruta − reembolsos − tarifas = líquida − custo = contribuição; "não é lucro nem caixa" | ☐ | | |
| 4.5 | Centro de comando → "Parar novas despesas" (e liberar) | Faixa de emergência; relatório do que foi pausado | ☐ | | |

## 5. Escritório 3D
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 5.1 | Sede inteira | 7 salas identificáveis, 13 personagens diferentes entre si | ☐ | | |
| 5.2 | Clicar em cada sala | Câmera foca a sala; painel com agentes e trabalhos recentes | ☐ | | |
| 5.3 | Estados (crie uma tarefa e observe) | Trabalhando = digitando; planejando = mão no queixo; aguardando = mão levantada; bloqueado = cabisbaixo; erro = mãos na cabeça; concluído = braço aberto | ☐ | | |
| 5.4 | Clicar num personagem | Painel do agente sem perder a cena; conversa contextual | ☐ | | |
| 5.5 | Qualidade gráfica Auto/Alta/Equilibrada/Baixa | Troca sem travar; fluido (o Mac mediu 60 FPS) | ☐ | | |
| 5.6 | Alternativas acessíveis da cena | Lista de agentes à direita (com estado) funciona por teclado; em computador sem WebGL o app troca sozinho para a vista 2D | ☐ | | |
| 5.7 | Arte e identidade (aceite artístico) | Divertido e legível, estilo "jogo de gestão" — **sua opinião** | ☐ | | |
| 5.8 | Nomes e placas das salas | Não se sobrepõem nem cobrem personagens; cada placa fica junto da sua sala | ☐ | | |

## 6. Comportamento de app de computador
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 6.1 | Fechar a janela | Vai para a bandeja (ou encerra, conforme Configurações) | ☐ | | |
| 6.2 | Bandeja → "Aprovações pendentes" | Abre a Central de Aprovações | ☐ | | |
| 6.3 | Notificação com a janela em segundo plano | Aviso do sistema numa aprovação nova | ☐ | | |
| 6.4 | Desligar a rede por 1 min e religar | Aviso "sem conexão" com dados em cache; ações bloqueadas; reconecta sozinho | ☐ | | |
| 6.5 | ⌘Q / Dock → Encerrar | Encerra na hora | ☐ | | |
| 6.6 | "Abrir ao ligar o computador" | Liga/desliga conforme a opção | ☐ | | |

## 7. Atualização (quando houver servidor publicado)
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 7.1 | Configurações → Atualizações → Verificar | "Você já está na versão mais nova" ou a versão nova com notas | ☐ | | |
| 7.2 | Instalar e reiniciar | Volta aberto na versão nova (testado: 0.1.0→0.1.1; pacote adulterado recusado) | ☐ | | |

## 8. Windows (dispensado pelo proprietário em 25/10/2026 — o instalador é testado no CI; opcional)
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 8.1 | Instalar o `.exe` (SmartScreen: "Mais informações → Executar assim mesmo") | Instala e abre | ☐ | | |
| 8.2 | Repetir 1.2, 2.1, 5.1, 5.5, 6.1 | Mesmo comportamento do Mac; anote modelo e placa de vídeo | ☐ | | |
| 8.3 | Desinstalar | Remove o app | ☐ | | |

## 9. Perfil Pessoa Física (CPF)
| # | Passo | Esperado | ☐ | Ajustes | Evidência |
|---|---|---|---|---|---|
| 9.1 | Configurações → Perfil do vendedor | "Pessoa Física (CPF)"; diz que o CPF não é guardado nem autoriza venda; lista requisitos pendentes | ☐ | | |
| 9.2 | Canais de venda | Cada canal: "Pessoa Física: Não verificada · Só simulação"; abrir "Elegibilidade por canal" mostra os 6 aspectos e as fontes | ☐ | | |
| 9.3 | Homologações → LEG-01 | "Elegibilidade da Pessoa Física para o modelo de venda"; requisitos com parecer; nada pedindo CNPJ | ☐ | | |
| 9.4 | Configurações → "Mudar para Pessoa Jurídica" (e cancelar) | Aviso de que as homologações do perfil voltam para revisão | ☐ | | |

**Resultado:** ☐ Aprovado para seguir para a próxima fase (integrações e homologações) · ☐ Ajustes pedidos (listados acima)
