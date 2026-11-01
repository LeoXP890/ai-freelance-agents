import { describe, expect, it } from "vitest";
import { sortRows } from "../desktop/table";

const rows = [{ n: "Pedido 10", v: 30 }, { n: "Pedido 2", v: 5 }, { n: "Ágata", v: 12 }];
const col = (sort: (r: (typeof rows)[number]) => string | number) => ({ key: "k", label: "k", render: () => null, sort });

describe("ordenação da DataTable", () => {
  it("números em ordem numérica, nos dois sentidos", () => {
    expect(sortRows(rows, col((r) => r.v), 1).map((r) => r.v)).toEqual([5, 12, 30]);
    expect(sortRows(rows, col((r) => r.v), -1).map((r) => r.v)).toEqual([30, 12, 5]);
  });
  it("texto em pt-BR com números naturais (Pedido 2 antes de Pedido 10) e acentos", () => {
    expect(sortRows(rows, col((r) => r.n), 1).map((r) => r.n)).toEqual(["Ágata", "Pedido 2", "Pedido 10"]);
  });
  it("sem coluna ordenável mantém a ordem original e não muta a entrada", () => {
    const copy = [...rows];
    expect(sortRows(rows, undefined, 1)).toBe(rows);
    sortRows(rows, col((r) => r.v), 1);
    expect(rows).toEqual(copy);
  });
});
