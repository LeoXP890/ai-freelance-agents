# Fase 3B — diagnóstico de homologação comercial (sem liberar LIVE)

## Objetivo

Dar aos agentes e ao operador uma visão **somente leitura** dos bloqueios do GoLivePolicy por canal, sem pedir ao proprietário uma lista manual de documentação. A execução continua sob controle dos gates, dos provedores e da aprovação humana.

## API

`GET /v1/go-live/readiness` (JWT com `golive:evaluate`).

Parâmetros opcionais: `uf`, `supplier_origin` (`NATIONAL` ou `INTERNATIONAL`), `fulfillment_mode` (`DROPSHIP_SUPPLIER`, `OWN_STOCK` ou `CROSS_BORDER`), `product_id` e `supplier_id`.

- Sem UF de origem (explícita ou cadastrada no perfil), origem do fornecedor ou modalidade logística, a resposta é **NOT_EVALUATED**, e não aprovação por padrão. A UF não é inferida do IP do usuário.
- Com escopo completo, o backend invoca a **mesma regra pura** `golive.evaluate` já usada em `POST /v1/go-live/evaluate` para os quatro canais. Apresenta `required_gates`, `unmet` e as restrições para cada um.
- `next_actions` agrega por código os motivos de bloqueio e os canais afetados. É orientação para obter evidências; não concede permissão, não realiza automação de contratação e não modifica o banco.
- O campo `provider_account_verified` é sempre `false` neste relatório: nenhuma conta, API, credencial ou transação é interrogada. **Não confundir ausência de bloqueios de política com prova de integração real.**
- `commercial_activation_authorized` e `live_operation_enabled_by_this_report` são sempre `false`. A mudança de modo, as autorizações de publicação, o pagamento, a compra e a emissão permanecem em fluxos separados.

Exemplo de consulta informativa, ainda sem qualquer ação externa:

```http
GET /v1/go-live/readiness?uf=PR&supplier_origin=NATIONAL&fulfillment_mode=DROPSHIP_SUPPLIER
Authorization: Bearer <token do operador>
```

A saída **não é uma lista de fornecedores homologados**. Pode haver outros bloqueios de credenciais, checkout, SLA e documentos contratuais que só são aferidos em testes de homologação com contas reais.

## Aceite desta entrega

- [ ] CI completo do PR verde (backend, frontend, regressão integrada).
- [ ] Testes comprovam `NOT_EVALUATED` sem escopo, `DENY` com gates e elegibilidade pendentes, equivalência com `POST /go-live/evaluate` e RBAC.
- [ ] Nenhuma credencial, segredo fiscal, CPF ou dado pessoal na resposta.
- [ ] Permanecem abertas as homologações LEG/TAX/SUP/CH/OPS, staging VPS e credenciais.

A **Fase 3 não pode ser declarada concluída** até haver evidências e testes reais. O relatório é ferramenta para agilizar a etapa, não substituto do processo.
