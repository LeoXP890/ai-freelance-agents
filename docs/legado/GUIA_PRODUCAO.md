# Guia do proprietário — do DRY_RUN à produção

**Situação em 24/10/2026:** o sistema está completo em **SIMULAÇÃO (DRY_RUN)**. Nenhuma venda, gasto, nota fiscal ou
publicação real aconteceu e nada foi homologado. Os passos abaixo dependem de **você** (contas, documentos,
contratos, decisões). Cada um diz onde entra no sistema e qual "gate" de homologação ele destrava — os gates ficam
em **Homologações** no app e só **você** aprova (com MFA). Sem os gates aprovados, o modo LIVE continua bloqueando
toda ação comercial, mesmo com credenciais configuradas.

**Atualização em 26/10/2026:** Mercado Livre é o canal prioritário; WooCommerce é complementar. A prévia Woo já está instalada privadamente, com visual aprovado, em localhost:8092 por túnel. [Onboarding ML e limites técnicos](readiness/MERCADO_LIVRE.md), [aceite visual](woo-store/ACEITE_VISUAL_2026-10-26.md) e [recuperação integral Woo](readiness/BACKUP_E_RECUPERACAO.md). O fluxo OAuth comercial existente exige LIVE: não habilitar LIVE apenas para testar conta; usar o diagnóstico isolado documentado. Aprovação visual não libera vendas.

Ordem atual: elegibilidade/fiscal/fornecedores → conta e integração Mercado Livre → homologação operacional → Woo público como canal complementar. Na VPS compartilhada já inspecionada, não executar bootstrap global, reiniciar serviços alheios ou alterar proxy/firewall seguindo exemplos genéricos deste guia.

---

## Etapa zero — staging fechado, sem vendas (recomendado antes de qualquer LIVE)

O deploy `./scripts/deploy.sh` existente usa `infra/env/live.env` com `AIC_MODE=LIVE`.
**Não o use como primeira homologação.** Para validar a VPS, backend, frontend, API, MFA, Docker,
observabilidade e escritório 3D com risco comercial zero, use um staging independente.

O novo staging é uma **simulação técnica isolada**, com base Postgres, Redis, segredos e
projeto Compose próprios. Somente a porta **127.0.0.1:8081** fica acessível na VPS:
o acesso remoto é exclusivamente por túnel SSH. Não recebe callbacks reais de marketplaces,
não habilita pagamentos e não comprova status de contas/fornecedores externos.

Na VPS, dentro do repositório já clonado, depois de preparar Docker:

```bash
python3 scripts/staging_preflight.py --init    # cria infra/env/staging.env (600) uma única vez
python3 scripts/staging_preflight.py --check   # falha se encontrar LIVE/credenciais/segredos fracos
bash scripts/deploy_staging.sh                 # somente DRY_RUN, no loopback
# backup e teste de restauração do staging (precisa do ambiente Python da API):
cd apps/api && AIC_ENV_FILE=../../infra/env/staging.env uv run python ../../scripts/backup.py backup \
  && AIC_ENV_FILE=../../infra/env/staging.env uv run python ../../scripts/backup.py restore-test; cd ../..
```
(Esse fluxo foi executado de ponta a ponta numa máquina local em 26/10/2026 — `docs/AUDITORIA_TECNICA_2026-10-26.md`
§2 —; na VPS o staging AI Commerce ainda não foi implantado. O acesso SSH foi posteriormente verificado para a prévia Woo separada.)

No seu computador, crie um túnel SSH (substitua usuário e IP pelos dados da sua VPS):

```bash
ssh -L 8081:127.0.0.1:8081 usuario@SEU_IP_DA_VPS
```

Então abra `http://127.0.0.1:8081` para validar a interface. A API de staging é
`http://127.0.0.1:8081/api`. Fechar o túnel interrompe o acesso externo. A stack
é `aicommerce-staging`, isolada da produção `aicommerce-live`. A verificação de
segredos é somente local: não publica, não envia nem registra o valor de nenhuma chave.

**Não marque LEG/TAX/SUP/CH/OPS como aprovados por ter staging saudável**. Antes de operar
comercialmente, conclua a Fase 3B com evidências de cada canal e faça a homologação
real usando o fluxo apropriado, separado deste staging.

---

## 1. Elegibilidade do vendedor — gate `LEG-01` (tarefa EXT-001) · perfil **Pessoa Física** (ADR 0006)
- O perfil escolhido é **Pessoa Física (CPF)** (Configurações → Perfil do vendedor). O sistema não pede nem guarda CPF
  e não trata o CPF como autorização. Detalhes: [`PERFIL_PESSOA_FISICA.md`](PERFIL_PESSOA_FISICA.md).
- `LEG-01` em PF = parecer de profissional habilitado sobre a venda pretendida por Pessoa Física (habitualidade,
  canais, venda à ordem, UF). Registrar como evidência **PARECER** — sem parecer o servidor recusa a aprovação.
- Para cada canal, a elegibilidade PF (cadastro, venda habitual, API, venda à ordem, envio, fiscal) é registrada pelo
  proprietário com MFA e fonte oficial em **Canais de venda → Elegibilidade por canal** (`PUT /v1/seller/eligibility/{canal}`).
  Hoje: todos "não verificados".
- (Perfil PJ continua suportado: CNPJ, regime e conta PJ como evidências do `LEG-01`, como no desenho original.)

## 2. Fiscal — gates `TAX-01`, `TAX-02`, `TAX-03` (EXT-002, EXT-003, EXT-004)
- **Contador**: parecer de venda nacional (dropshipping/venda à ordem) — `TAX-01`.
- **Importação** só se for vender de fornecedor internacional — `TAX-02` (sem ele o sistema bloqueia cross-border).
- **Documento fiscal da venda** — `TAX-03`: em Pessoa Física, primeiro o parecer do contador sobre qual documento é
  exigido e quem emite (você, o canal ou um serviço); só então emissor/certificado, se o parecer exigir. (Em PJ:
  credenciamento SEFAZ + certificado A1 + emissor de NF-e.) Hoje a emissão é simulada (documento fictício, sem valor fiscal); o adaptador real do
  emissor será ligado quando você escolher o provedor (precisa da documentação/credencial dele).
- O contador também aprova a **alíquota/perfil tributário** usado no preço (hoje marcado "não aprovado por contador").

## 3. Consumidor e LGPD — gate `OPS-01` (EXT-010)
- Política de trocas e devoluções (CDC, 7 dias de arrependimento), política de privacidade e termos de uso,
  revisados por advogado. Publicar no site (passo 5) e registrar a evidência no gate.

## 4. Fornecedor real — gate `SUP-01` (EXT-005)
- Contrato, documentos do fornecedor (CNPJ/registro dele, notas, certificados), amostra aprovada, SLA de despacho.
- No app: **Fornecedores** → cadastrar → registrar documentos → marcar verificados → amostra → *Aprovar fornecedor*
  (o checklist SUP-01 precisa estar completo; o servidor confere).

## 5. Loja WooCommerce — gate `CH-WOO` (EXT-009 para pagamento)

**Prévia privada sem domínio:** a nova loja isolada e seu tema estão documentados em [WooCommerce privado — entrega](woo-store/RELATORIO.md). Essa etapa usa túnel SSH, bloqueia compras e não executa os passos LIVE abaixo. Não requer WordPress.com nem contratação de hospedagem adicional.

**O adaptador real já está pronto e foi testado contra um WooCommerce de verdade** (publicar, atualizar, pausar,
reconciliar, sem duplicar produto em retry — `scripts/woo_live_check.py`). Falta só a sua loja:
1. Hospedagem + domínio com **HTTPS** (ex.: Hostinger com WordPress). Instalar WordPress + WooCommerce.
2. Meio de pagamento no Woo que **aceite Pessoa Física** (confirmar com o prestador) e seja compatível com o parecer
   fiscal — conta do gateway em nome do vendedor (EXT-009).
3. Publicar as políticas do passo 3 no site.
4. WooCommerce → Configurações → Avançado → **REST API** → *Adicionar chave* (usuário administrador, permissão
   **Leitura/Escrita**). Copie a *Consumer key* e o *Consumer secret* (aparecem uma vez).
5. WooCommerce → Configurações → Avançado → **Webhooks** → criar webhooks de *Pedido criado* e *Pedido atualizado*
   para `https://SEU_SERVIDOR/api/v1/webhooks/woocommerce`, com um **segredo** forte.
6. No servidor (arquivo `infra/env/live.env`, nunca no código/chat):
   ```
   AIC_WOO_BASE_URL=https://sualoja.com.br
   AIC_WOO_CONSUMER_KEY=ck_...
   AIC_WOO_CONSUMER_SECRET=cs_...
   AIC_WOO_WEBHOOK_SECRET=<o mesmo segredo do webhook>
   ```
7. Teste num produto de verificação e registre a evidência no gate `CH-WOO`; depois aprove o gate.

## 6. Servidor (VPS) — QA-006
O kit de implantação está pronto (HTTPS automático, segredos gerados, backup diário cifrado com cópia externa,
teste de restauração semanal, monitoramento e volta para a versão anterior). Você já tem a VPS; confira:
- Sistema **Ubuntu 24.04** (se for outro, me avise), pelo menos 2 vCPU, 4 GB de RAM e 40 GB livres, e acesso SSH.
- Um domínio para a API (ex.: `api.sualoja.com.br`) com **registro A** apontando para o IP da VPS.

Na VPS (uma vez):
```bash
git clone git@github.com:LeoXP890/ai-commerce.git /opt/ai-commerce && cd /opt/ai-commerce
sudo ./scripts/vps_bootstrap.sh                                   # Docker, firewall, backups agendados
python3 scripts/gen_live_env.py --domain api.sualoja.com.br       # infra/env/live.env com segredos fortes
./scripts/deploy.sh                                               # sobe com HTTPS e confere /api/healthz
```
- **Guarde uma cópia** de `infra/env/live.env` e `infra/env/secrets/backup_passphrase` fora da VPS (gerenciador
  de senhas). Sem eles, backups e dados pessoais cifrados não podem ser recuperados.
- **Cópia dos backups fora da VPS**: crie um destino no rclone (Backblaze B2, S3 ou o seu Google Drive):
  `docker run --rm -it -v $PWD/infra/env/secrets:/config/rclone rclone/rclone:1.68.2 config` e coloque
  `AIC_BACKUP_REMOTE=<nome>:<pasta>` no `live.env`. O backup das 03:30 passa a subir a cópia cifrada.
- **Primeiro usuário (você)** — gera uma senha forte e a mostra UMA vez (anote; dá para trocar depois); no 1º
  login o app pede para ativar o MFA:
  `docker compose -f infra/compose/docker-compose.yml -f infra/compose/production.yml --env-file infra/env/live.env
  exec -it api python -m aicommerce.manage create-user --email voce@sualoja.com.br --name "Seu nome" --role owner`
- No app desktop: Configurações → Servidor → `https://api.sualoja.com.br/api`.
- **Monitoramento** (Prometheus + Grafana + alertas): `./scripts/observability.sh up` na VPS (usa o token do
  `live.env`); acesso pelo túnel SSH (`ssh -L 3001:localhost:3001 …`) — não fica exposto na internet.
- **Voltar para a versão anterior** se algo der errado depois de atualizar: `./scripts/deploy.sh --rollback`
  (usa o registro de versões saudáveis; banco não é revertido — as migrações são só aditivas).
- Atualizações depois: `./scripts/deploy.sh` na VPS, ou pelo GitHub (Actions → **deploy**) após cadastrar os
  segredos `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_KNOWN_HOSTS`, `VPS_APP_DIR` (instruções no `deploy.yml`).
- Alertas por e-mail/WhatsApp: `docs/runbook/alertas.md`.

## 7. Marketplaces — gates `CH-ML`, `CH-SHO`, `CH-TTS` (EXT-006, EXT-007, EXT-008)
- **Mercado Livre** — o cliente real está pronto (publicar, atualizar preço/estoque/fotos, pausar, sem duplicar em
  nova tentativa, renovação automática do acesso). Falta a sua conta:
  1. Conta de vendedor (Pessoa Física, elegibilidade confirmada) e uma aplicação no **DevCenter do Mercado Livre** com a URL de retorno
     `https://api.sualoja.com.br/api/v1/channels/MERCADO_LIVRE/oauth/callback`.
  2. No `live.env`: `AIC_ML_APPLICATION_ID`, `AIC_ML_CLIENT_SECRET`, `AIC_ML_REDIRECT_URI` (modelo no
     `live.env.example`) e `./scripts/deploy.sh`.
  3. Notificações da aplicação para `https://api.sualoja.com.br/api/v1/webhooks/mercadolivre`.
  4. No app: **Canais de venda → Conectar conta real** (precisa de login com MFA); autorize no site do ML.
  5. Mapeie as categorias reais (anúncios com categoria simulada são recusados antes de chegar ao ML), teste um
     anúncio e aprove o gate `CH-ML`.
- **Shopee** e **TikTok Shop**: conta de vendedor + acesso de desenvolvedor/parceiro. Os contratos de API estão
  marcados **UNVERIFIED** porque a documentação oficial exige login de parceiro — com o acesso, conferimos campos
  e assinaturas e ligamos o cliente real (COM-006, COM-007). TikTok Shop não aceita fornecedor internacional
  (bloqueio já implementado).

## 8. IA real dos agentes (hoje: mock, sem custo)
- Quando quiser IA real: chave do **Google AI Studio** (Gemini). No `live.env`:
  ```
  AIC_LLM_PROVIDER=google
  AIC_GOOGLE_API_KEY=<sua chave>
  AIC_AI_MONTHLY_BUDGET_MICRO_USD=20000000   # teto mensal: US$ 20 (ajuste)
  ```
- Modelos padrão `gemini-2.5-flash` (rotina) e `gemini-2.5-pro` (casos complexos). **Confira a tabela de preços
  oficial** antes e atualize `PRICES` em `apps/api/src/aicommerce/ai/llm.py` se mudou. O teto mensal é reservado
  antes de cada chamada (nunca ultrapassa) e cada execução tem limites próprios.
- Antes de ligar: `AIC_LLM_PROVIDER=google uv run python -m aicommerce.ai.evals --report …` (custa centavos) e
  compare com o relatório do mock.
- **Busca semântica da base de conhecimento** (acha "case para smartphone" num texto sobre "capinha de celular"):
  `AIC_KB_EMBEDDER=google` no `live.env`, `./scripts/deploy.sh` e depois
  `… exec api python -m aicommerce.manage reindex-kb` (regrava os vetores; custa frações de centavo por documento,
  dentro do mesmo teto mensal). Sem chave ou sem saldo, a busca continua funcionando só por texto.
  A similaridade mínima do Gemini (0,5) é um ponto de partida: confira os resultados com a sua base real.
  **Antes de ligar de vez**, meça: `AIC_KB_EMBEDDER=google … python -m aicommerce.ai.kb_eval` — o relatório só
  aprova (`semantic_ready_for_production`) se acertar ≥ 75% das perguntas feitas com outras palavras (hoje, só com
  texto: 100% das perguntas com as mesmas palavras e 42% das paráfrases).

## 9. App desktop — atualizações e assinatura (UXD-021, UXD-024)
- **Atualização automática já funciona** (testada no app real: 0.1.0 → 0.1.1 sozinho; pacote adulterado recusado).
  Não depende da Apple: os pacotes são assinados com a chave do updater, que fica em
  `infra/env/secrets/tauri_updater.key` + `.password` (fora do git). **Guarde uma cópia em local seguro** — sem ela
  os apps instalados não aceitam versões novas (seria preciso reinstalar à mão).
  Publicar uma versão: aumente `version` em `apps/desktop/src-tauri/tauri.conf.json` e `Cargo.toml`, rode
  `./scripts/build_desktop_release.sh` e siga os comandos que ele imprime (publica no servidor). Os apps avisam e
  instalam em Configurações → Este computador → Atualizações. Tirar uma versão do ar: `manage revoke-desktop-release`.
- **macOS**: conta Apple Developer (US$ 99/ano) → certificado *Developer ID Application* → assinatura e
  notarização (some o aviso "desenvolvedor não identificado" e o pedido do Keychain a cada versão).
- **Windows**: o instalador é gerado e testado (instalar → abrir → desinstalar) num Windows real no GitHub Actions
  (workflow `desktop-windows`; baixe o artefato). Sem certificado de código o Windows SmartScreen avisa.

## 10. Ir para produção
1. Todos os gates necessários **APROVADOS** por você em Homologações (Empresa, Fiscal, Consumidor, Fornecedor e o
   canal que vai abrir).
2. No servidor, `AIC_MODE=LIVE` no `live.env` e reiniciar. Em LIVE o sistema **recusa iniciar** com qualquer
   segredo de simulação.
3. Primeiro produto: preparar o anúncio → propor publicação → aprovar com MFA → conferir na loja.
4. Kill switch disponível no Centro de comando ("Parar novas despesas") e no runbook.

## Versionamento (GitHub)
- Repositório **privado**: https://github.com/LeoXP890/ai-commerce. O CI roda a cada envio (testes, stack completa,
  segurança). O envio a partir deste Mac usa SSH.
- Segredos e dados pessoais nunca vão para o Git (`infra/env/*.env`, `infra/env/secrets/` estão ignorados).
