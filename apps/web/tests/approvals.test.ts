import { describe, expect, it } from "vitest";
import { parseDetail } from "../lib/api";
import { affectsApprovals, approvalErrorMessage, formatMinor, shortHash } from "../lib/format";

describe("inbox de aprovações", () => {
  it("formata minor units em BRL", () => {
    expect(formatMinor(899000).replace(/\s/g, " ")).toBe("R$ 8.990,00");
    expect(formatMinor(5).replace(/\s/g, " ")).toBe("R$ 0,05");
  });

  it("encurta hash", () => {
    expect(shortHash("a".repeat(64))).toBe("aaaaaaaa…aaaaaa");
    expect(shortHash("abc")).toBe("abc");
  });

  it("normaliza detail do FastAPI", () => {
    expect(parseDetail("x", "f")).toEqual({ message: "x", code: null });
    expect(parseDetail({ code: "STALE_PAYLOAD", message: "mudou" }, "f")).toEqual({ message: "mudou", code: "STALE_PAYLOAD" });
    expect(parseDetail([{ msg: "campo obrigatório" }, { msg: "inválido" }], "f"))
      .toEqual({ message: "campo obrigatório; inválido", code: "VALIDATION" });
    expect(parseDetail(undefined, "f")).toEqual({ message: "f", code: null });
  });

  it("mensagem específica para payload desatualizado", () => {
    expect(approvalErrorMessage("STALE_PAYLOAD", "x")).toMatch(/Recarregue/);
    expect(approvalErrorMessage(null, "original")).toBe("original");
  });

  it("toda ação conhecida da política tem rótulo legível", async () => {
    const { ACTION_LABEL } = await import("../lib/format");
    for (const a of ["RESEARCH", "PREPARE_LISTING_DRAFT", "PUBLISH_LISTING", "UPDATE_LISTING_MATERIAL", "PAUSE_LISTING",
      "CREATE_CAMPAIGN", "INCREASE_BUDGET", "SPEND", "PAUSE_MEDIA", "REFUND", "CHANGE_SUPPLIER", "SUPPLIER_PURCHASE"]) {
      expect(ACTION_LABEL[a]).toBeTruthy();
    }
  });

  it("eventos que recarregam o inbox", () => {
    for (const t of ["approval.requested", "approval.decided", "approval.expired", "execution.failed",
      "listing.publish.confirmed", "integration.unknown_result", "emergency_stop.activated"]) {
      expect(affectsApprovals(t)).toBe(true);
    }
    expect(affectsApprovals("agent.task.started")).toBe(false);
  });
});
