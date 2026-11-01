// Composição explícita dos números financeiros (finalização, seção 9): o que ENTRA em cada cálculo e o que fica de
// fora. Contribuição NÃO é lucro líquido nem fluxo de caixa.
import type { SalesDashboard } from "./api";

export interface CompositionRow { key: string; label: string; value: number; op: "+" | "−" | "="; hint: string }

export const NOT_INCLUDED = [
  "mídia paga (campanhas)",
  "frete pago por você",
  "tributos (perfil a aprovar pelo contador)",
  "custos fixos (plataforma, domínio, VPS, pessoas)",
  "custo de IA dos agentes",
] as const;

export function composition(channels: SalesDashboard["channels"]): CompositionRow[] {
  const sum = (f: (c: SalesDashboard["channels"][number]) => number | undefined) =>
    channels.reduce((a, c) => a + (f(c) ?? 0), 0);
  const gross = sum((c) => c.revenue_minor), refunds = sum((c) => c.refund_minor), fees = sum((c) => c.fees_minor);
  const cost = sum((c) => c.cost_minor);
  const net = gross - refunds - fees;
  return [
    { key: "gross", label: "Receita bruta", value: gross, op: "+", hint: "pedidos pagos no período (inclusive os depois reembolsados)" },
    { key: "refunds", label: "Reembolsos", value: refunds, op: "−", hint: "valor devolvido aos clientes" },
    { key: "fees", label: "Tarifas do canal", value: fees, op: "−", hint: "comissões esperadas pela tabela vigente" },
    { key: "net", label: "Receita líquida", value: net, op: "=", hint: "bruta − reembolsos − tarifas" },
    { key: "cost", label: "Custo do fornecedor", value: cost, op: "−", hint: "custo das mercadorias vendidas" },
    { key: "contribution", label: "Contribuição", value: net - cost, op: "=", hint: "líquida − custo do fornecedor" },
  ];
}
