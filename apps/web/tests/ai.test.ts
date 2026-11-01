import { describe, expect, it } from "vitest";
import { formatMicroUsd, SKILL_INPUT } from "../lib/format";

describe("painéis de IA", () => {
  it("formata custo em micro-dólares", () => {
    expect(formatMicroUsd(350)).toBe("US$ 0.00035");
    expect(formatMicroUsd(20_000_000)).toBe("US$ 20.00");
  });
  it("toda habilidade conhecida tem tipo de entrada", () => {
    for (const k of ["research.opportunities", "compliance.review", "supplier.shortlist", "commercial.model",
      "listing.audit", "creative.copy", "ads.plan", "support.reply_draft", "finance.brief", "analytics.report",
      "logistics.sla_check"]) expect(SKILL_INPUT[k]).toBeTruthy();
  });
});
