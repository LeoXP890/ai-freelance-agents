import { describe, expect, it } from "vitest";
import { composition, NOT_INCLUDED } from "../lib/financeTerms";

const ch = (o: Record<string, number>) => ({ channel: "WOOCOMMERCE", orders: 1, revenue_minor: 0, net_revenue_minor: 0,
  contribution_minor: 0, avg_ticket_minor: 0, cancel_rate_bps: 0, margin_bps: 0, refunded: 0, ...o });

describe("composição financeira explícita", () => {
  it("receita líquida e contribuição batem com o que o backend calcula, somando os canais", () => {
    const rows = composition([
      ch({ revenue_minor: 8063, refund_minor: 8063, fees_minor: 322, cost_minor: 3290 }),
      ch({ revenue_minor: 10000, fees_minor: 500, cost_minor: 4000 }),
    ]);
    const v = (k: string) => rows.find((r) => r.key === k)!.value;
    expect(v("gross")).toBe(18063);
    expect(v("net")).toBe(18063 - 8063 - 822);
    expect(v("contribution")).toBe(v("net") - 7290);
  });
  it("deixa claro que contribuição não é lucro: mídia, frete, tributos, fixos e IA ficam de fora", () => {
    expect(NOT_INCLUDED.join(" ")).toMatch(/mídia.*frete.*tributos.*fixos.*IA/);
  });
});
