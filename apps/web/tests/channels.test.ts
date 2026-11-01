import { describe, expect, it } from "vitest";
import { accountFor, channelHealth, gatePending } from "../lib/channels";

const NOW = Date.parse("2026-10-24T12:00:00Z");
const acc = (o: Partial<{ status: string; expires_at: string | null; simulated: boolean; external_account_id: string }>) => ({
  channel: "MERCADO_LIVRE", status: "CONNECTED", expires_at: "2026-10-25T00:00:00Z", simulated: false, external_account_id: "123", ...o,
});

describe("estado do canal (sem verde genérico)", () => {
  it("conta simulada é Mock; Woo sem conta em DRY_RUN também", () => {
    expect(channelHealth("MERCADO_LIVRE", acc({ simulated: true }), true, NOW).label).toBe("Mock");
    expect(channelHealth("WOOCOMMERCE", undefined, true, NOW).label).toBe("Mock");
  });
  it("sem conta fora de simulação é Não conectado", () => {
    expect(channelHealth("SHOPEE", undefined, false, NOW).label).toBe("Não conectado");
    expect(channelHealth("SHOPEE", undefined, true, NOW).label).toBe("Não conectado");
  });
  it("renovação recusada ou conta revogada é Problema, mesmo simulada", () => {
    const h = channelHealth("SHOPEE", acc({ simulated: true, status: "REAUTH_REQUIRED" }), true, NOW);
    expect([h.label, h.tone]).toEqual(["Problema", "crit"]);
    expect(h.detail).toMatch(/reconectar/);
    expect(channelHealth("SHOPEE", acc({ status: "REVOKED" }), false, NOW).label).toBe("Problema");
  });
  it("token de acesso vencido com conta conectada NÃO é problema (servidor renova antes de usar)", () => {
    const h = channelHealth("MERCADO_LIVRE", acc({ expires_at: "2026-10-24T10:00:00Z" }), false, NOW);
    expect(h.label).toBe("Conectado");
    expect(h.detail).toMatch(/renova no próximo uso/);
  });
  it("conta do modo atual vale: real em LIVE, simulada em DRY_RUN; substituída não", () => {
    const sim = acc({ simulated: true, external_account_id: "SIM" });
    const real = acc({ external_account_id: "999" });
    const old = acc({ external_account_id: "111", status: "REPLACED" });
    expect(accountFor("MERCADO_LIVRE", [sim, old, real], false)?.external_account_id).toBe("999");
    expect(accountFor("MERCADO_LIVRE", [old, real, sim], true)?.external_account_id).toBe("SIM");
    expect(accountFor("MERCADO_LIVRE", [old], false)).toBeUndefined();
  });
  it("conta real válida é Conectado; homologação pendente é independente", () => {
    expect(channelHealth("MERCADO_LIVRE", acc({}), false, NOW).label).toBe("Conectado");
    expect(gatePending("NOT_STARTED")).toBe(true);
    expect(gatePending("APPROVED_WITH_RESTRICTIONS")).toBe(false);
  });
});
