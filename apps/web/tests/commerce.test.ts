import { describe, expect, it } from "vitest";
import { affectsCommerce, formatBps, hoursSince, LISTING_STATUS, SUPPLIER_STATUS } from "../lib/format";

describe("painéis Nexus/Nova", () => {
  it("formata margem em bps", () => {
    expect(formatBps(1500)).toBe("15,0%");
    expect(formatBps(-250)).toBe("-2,5%");
  });
  it("idade da observação de estoque em horas", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    expect(hoursSince("2026-10-23T11:00:00Z", now)).toBe(25);
    expect(hoursSince("2026-10-24T11:30:00Z", now)).toBe(0);
  });
  it("eventos de comércio recarregam painéis", () => {
    for (const t of ["supplier.status_changed", "supplier.stock.stale", "listing.prepared", "listing.publish.confirmed",
      "catalog.product.created", "webhook.received", "execution.failed"]) expect(affectsCommerce(t)).toBe(true);
    expect(affectsCommerce("agent.task.started")).toBe(false);
  });
  it("rótulos cobrem os estados do backend", () => {
    for (const s of ["DRAFT", "PENDING_APPROVAL", "PUBLISHED", "PAUSED", "ERROR", "DELETED_EXTERNALLY"]) {
      expect(LISTING_STATUS[s]).toBeTruthy();
    }
    for (const s of ["PROSPECT", "EVALUATING", "APPROVED", "SUSPENDED", "REJECTED"]) expect(SUPPLIER_STATUS[s]).toBeTruthy();
  });
});

describe("dashboard e gates", async () => {
  const { barPct, GATE_STATE } = await import("../lib/format");
  it("escala de barras com piso visível", () => {
    expect(barPct(0, 100)).toBe(0);
    expect(barPct(1, 1000)).toBe(2);
    expect(barPct(50, 100)).toBe(50);
    expect(barPct(10, 0)).toBe(0);
  });
  it("estados de gate rotulados", () => {
    for (const s of ["NOT_STARTED", "COLLECTING_EVIDENCE", "IN_REVIEW", "APPROVED", "APPROVED_WITH_RESTRICTIONS",
      "REJECTED", "EXPIRED"]) expect(GATE_STATE[s]).toBeTruthy();
  });
});
