import { describe, expect, it } from "vitest";
import { affectsCommerce, nextOrderAction, ORDER_STATUS } from "../lib/format";

describe("painéis de operação", () => {
  it("próxima ação segue a máquina de estados", () => {
    expect(nextOrderAction("READY_TO_FULFILL", null)).toBe("fulfill");
    expect(nextOrderAction("READY_TO_FULFILL", "CANCELLED")).toBe("fulfill");
    expect(nextOrderAction("READY_TO_FULFILL", "REQUESTED")).toBeNull();
    expect(nextOrderAction("SUPPLIER_ACCEPTED", "ACCEPTED")).toBe("ship");
    expect(nextOrderAction("SHIPPED", "SHIPPED")).toBe("deliver");
    expect(nextOrderAction("DELIVERED", "DELIVERED")).toBeNull();
  });
  it("todos os estados do pedido têm rótulo", () => {
    for (const s of ["NEW", "PAYMENT_CONFIRMED", "FISCAL_PENDING", "READY_TO_FULFILL", "SUPPLIER_ACCEPTED", "SHIPPED",
      "DELIVERED", "CANCELLED", "RETURN_REQUESTED", "REFUNDED", "INCIDENT"]) expect(ORDER_STATUS[s]).toBeTruthy();
  });
  it("eventos de pedido recarregam painéis", () => {
    for (const t of ["order.received", "order.status_changed", "return.requested", "settlement.imported",
      "invoice.authorized"]) expect(affectsCommerce(t)).toBe(true);
  });
});
