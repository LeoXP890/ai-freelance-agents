# Alertas por e-mail e WhatsApp

Destinos (e-mail e WhatsApp do proprietário) configurados em `infra/env/alerting.env` — arquivo local, fora do git;
dados pessoais não ficam no repositório.

| Tipo de alerta | E-mail | WhatsApp | Repete enquanto aberto |
|---|---|---|---|
| Crítico (API/worker/banco fora, alerta operacional crítico) | sim | sim | a cada 2 h |
| Kill switch acionado | sim | sim | a cada 12 h |
| Aviso (5xx, fila travada, backlog, orçamento de IA) | sim | não | a cada 12 h |
| Resolução | sim | sim (se era crítico) | — |

As mensagens trazem só nome do alerta, severidade, resumo e horário — nunca dados de clientes.

## 1. Ativar o e-mail (Gmail) — ~3 minutos

O Gmail não aceita a senha normal de programas; é preciso uma **senha de app**:

1. Verificação em duas etapas ligada na conta Google (obrigatório para senhas de app).
2. Acesse <https://myaccount.google.com/apppasswords>, crie uma senha chamada "AI Commerce alertas".
3. No terminal, grave a senha num arquivo (não cole em chat, e-mail ou código):
   ```bash
   read -rs SENHA && printf '%s' "$SENHA" > infra/env/secrets/smtp_password && unset SENHA
   ./scripts/observability.sh reload
   ```
   A mensagem deve mostrar `e-mail: ATIVO`.

## 2. Ativar o WhatsApp (API oficial da Meta) — exige conta de empresa

O WhatsApp não permite que programas enviem mensagens por um número pessoal; o caminho oficial é a
**WhatsApp Cloud API**:

1. Em <https://business.facebook.com>, crie (ou use) um portfólio empresarial e um app do tipo **Business** em
   <https://developers.facebook.com/apps>, adicionando o produto **WhatsApp**.
2. Use o número de teste que a Meta fornece ou registre um número **diferente** do seu pessoal como remetente.
   Anote o **Phone number ID**.
3. Adicione o seu número de WhatsApp (o mesmo de `infra/env/alerting.env`) como destinatário (no número de teste, ele precisa ser verificado por código).
4. Crie um **modelo de mensagem** (WhatsApp Manager → Modelos), categoria *Utilidade*, idioma *Português (BR)*,
   nome `aic_alerta`, corpo: `Alerta do AI Commerce: {{1}}`. Aguarde a aprovação.
5. Gere um **token permanente** (usuário do sistema com permissão `whatsapp_business_messaging`) e grave:
   ```bash
   read -rs TOKEN && printf '%s' "$TOKEN" > infra/env/secrets/whatsapp_token && unset TOKEN
   ```
6. Em `infra/env/alerting.env`: `AIC_WHATSAPP_PROVIDER=meta` e `AIC_WHATSAPP_PHONE_NUMBER_ID=<id do passo 2>`.
   Confira `AIC_WHATSAPP_API_VERSION` com a versão vigente mostrada no painel da Meta.
7. `./scripts/observability.sh reload`.

Observação: alguns números de celular brasileiros aparecem na API sem o nono dígito (`554398594647`). Se a Meta
recusar o destino, confira o formato exato na lista de destinatários do painel.

## 3. Testar sem esperar uma falha real

```bash
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -s -X POST localhost:9093/api/v2/alerts -H 'Content-Type: application/json' \
  -d "[{\"labels\":{\"alertname\":\"TesteManual\",\"severity\":\"critical\"},\"annotations\":{\"summary\":\"teste\"},\"startsAt\":\"$NOW\"}]"
```
Em ~30 s chegam e-mail e WhatsApp. Sem credenciais, o relay só registra o texto:
`docker compose -f infra/compose/observability.yml --env-file infra/env/observability.env logs alert-relay`.

## Como funciona

Prometheus avalia as regras → **Alertmanager** agrupa/repete/suprime → e-mail direto (SMTP do Gmail) e, para
críticos, webhook autenticado para o **relay** (`aicommerce.alert_relay`), que chama a API do WhatsApp.
Senhas e tokens ficam só em `infra/env/secrets/` (arquivos, fora do git); o `alertmanager.yml` gerado guarda
apenas o caminho deles.
