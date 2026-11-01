import { describe, expect, it } from "vitest";
import { type EligibilityRow, liveExplanation } from "../lib/seller";

const row = (status: EligibilityRow["status"], aspects: EligibilityRow["aspects"] = {}): EligibilityRow =>
  ({ channel: "SHOPEE", seller_type: "PERSON", status, registered: true, aspects, restrictions: [], pending: [], sources: [], note: "" });

describe("elegibilidade por canal na interface", () => {
  it("canal não verificado para PF aparece como só simulação, com os aspectos pendentes", () => {
    const e = liveExplanation(row("UNVERIFIED", { fiscal: { status: "UNVERIFIED", note: "", label: "Obrigações fiscais" } }), "PERSON");
    expect(e.simOnly).toBe(true);
    expect(e.text).toContain("Pessoa Física");
    expect(e.text).toContain("obrigações fiscais");
    expect(e.text).not.toContain("CNPJ");  // não presume exigência não confirmada
  });
  it("incompatível também é só simulação; comprovado ainda lembra das homologações", () => {
    expect(liveExplanation(row("UNSUPPORTED"), "PERSON").simOnly).toBe(true);
    const ok = liveExplanation(row("SUPPORTED"), "PERSON");
    expect(ok.simOnly).toBe(false);
    expect(ok.text).toContain("homologações");
  });
  it("PJ sem matriz segue o desenho original (gate do canal)", () => {
    expect(liveExplanation(undefined, "COMPANY").simOnly).toBe(false);
  });
});
