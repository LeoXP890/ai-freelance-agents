import { describe, expect, it } from "vitest";
import { approvalPriority, describeEvent, expiresIn, formatDuration, pathLabel, payloadRows, riskLevel, showValue, sortInbox } from "../lib/workflow";

const NOW = Date.parse("2026-10-24T12:00:00Z");
const p = (over: Partial<{ status: string; expires_at: string; risk_score: number; monetary_scope_minor: number; created_at: string }>) => ({
  status: "PENDING_APPROVAL", expires_at: "2026-10-27T12:00:00Z", risk_score: 10, monetary_scope_minor: 0,
  created_at: "2026-10-24T10:00:00Z", ...over,
});

describe("prioridade da inbox de aprovações", () => {
  it("marca urgente só pendente que expira em menos de 24 h", () => {
    expect(approvalPriority(p({ expires_at: "2026-10-25T06:00:00Z" }), NOW).urgent).toBe(true);
    expect(approvalPriority(p({ expires_at: "2026-10-26T06:00:00Z" }), NOW).urgent).toBe(false);
    expect(approvalPriority(p({ status: "SUCCEEDED", expires_at: "2026-10-24T13:00:00Z" }), NOW).urgent).toBe(false);
  });
  it("risco e valor vêm do backend (score e escopo monetário)", () => {
    const x = approvalPriority(p({ risk_score: 75, monetary_scope_minor: 5000 }), NOW);
    expect([x.risk, x.value]).toEqual([true, true]);
  });
  it("ordena pendentes urgentes primeiro, depois risco, valor e decididas por último", () => {
    const items = [
      { ...p({ status: "SUCCEEDED" }), id: "done" },
      { ...p({}), id: "normal" },
      { ...p({ monetary_scope_minor: 100 }), id: "valor" },
      { ...p({ risk_score: 90 }), id: "risco" },
      { ...p({ expires_at: "2026-10-24T15:00:00Z" }), id: "urgente" },
    ];
    expect(sortInbox(items, NOW).map((i) => i.id)).toEqual(["urgente", "risco", "valor", "normal", "done"]);
  });
  it("texto de validade", () => {
    expect(expiresIn("2026-10-24T12:30:00Z", NOW)).toBe("expira em 30 min");
    expect(expiresIn("2026-10-25T08:00:00Z", NOW)).toBe("expira em 20 h");
    expect(expiresIn("2026-10-20T08:00:00Z", NOW)).toBe("expirada");
  });
});

describe("conteúdo proposto legível", () => {
  it("formata dinheiro, booleanos e rótulos", () => {
    const rows = payloadRows({ refund_minor: 8063, withdrawal_right: true, channel: "WOOCOMMERCE", custom_key: null });
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ["Reembolso", "R$ 80,63"], ["Direito de arrependimento", "Sim"], ["Canal", "WOOCOMMERCE"], ["Custom key", "—"],
    ]);
  });
  it("traduz o contrato do Mercado Livre (preço em reais, listas legíveis)", () => {
    const rows = Object.fromEntries(payloadRows({ price: 89.9, currency_id: "BRL", pictures: [], condition: "new",
      attributes: [{ id: "SELLER_SKU", value_name: "S1" }] }).map((r) => [r.label, r.value]));
    expect(rows).toEqual({ "Preço": "R$\u00a089,90", Moeda: "BRL", Imagens: "nenhum", "Condição": "Novo", Atributos: "SELLER_SKU: S1" });
  });
});

describe("missões", () => {
  it("duração legível", () => {
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(125)).toBe("2 min 5 s");
    expect(formatDuration(7260)).toBe("2 h 1 min");
  });
  it("descreve eventos reais da linha do tempo", () => {
    const base = { type: "agent.task.state_changed", from: null, tool: null, detail: {} };
    expect(describeEvent({ ...base, task_state: "WAITING_EXTERNAL", detail: { children: 3 } })).toBe("Aguardando ferramenta (3 etapas)");
    expect(describeEvent({ ...base, task_state: "FAILED", detail: { error_kind: "LIMIT_REACHED" } })).toBe("Falhou — LIMIT_REACHED");
    expect(describeEvent({ ...base, task_state: null, tool: "kb.search" })).toBe("Usou a ferramenta kb.search");
    expect(describeEvent({ ...base, task_state: "CANCELLED", detail: { reason: "mission_cancelled" } })).toBe("Cancelada — missão cancelada");
  });
});

describe("comparação de versões da proposta", () => {
  it("mostra ausência, vazio, números e estruturas de forma legível", () => {
    expect(showValue(undefined)).toBe("—");
    expect(showValue(null)).toBe("—");
    expect(showValue("")).toBe("(vazio)");
    expect(showValue(74.9)).toBe("74.9");
    expect(showValue(false)).toBe("false");
    expect(showValue([{ source: "a.jpg" }])).toBe('[{"source":"a.jpg"}]');
  });
  it("nomeia os campos como no resumo, com posição contada a partir de 1", () => {
    expect(pathLabel("price")).toBe("Preço");
    expect(pathLabel("pictures[0].source")).toBe("Imagens 1 · source");
    expect(pathLabel("attributes[2].value_name")).toBe("Atributos 3 · value_name");
    expect(pathLabel("campo_novo")).toBe("campo_novo");
  });
});

describe("duração", () => {
  it("abaixo de 1 segundo não aparece como 0 s", () => {
    expect(formatDuration(0)).toBe("< 1 s");
    expect(formatDuration(0.4)).toBe("< 1 s");
    expect(formatDuration(42)).toBe("42 s");
  });
});

describe("risco legível na caixa de aprovações", () => {
  it("escore vira baixo/médio/alto", () => {
    expect(riskLevel(10).label).toBe("Risco baixo");
    expect(riskLevel(40)).toEqual({ label: "Risco médio", tone: "warn" });
    expect(riskLevel(85).tone).toBe("crit");
  });
});
