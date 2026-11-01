import { describe, expect, it } from "vitest";
import { declutter, nearby, platePriority } from "../lib/plates";

const P = (id: string, x: number, y: number, priority = 0) => ({ id, x, y, w: 80, h: 20, priority });
const rect = (p: ReturnType<typeof P>, d: { dx: number; dy: number }) => ({ l: p.x - 40 + d.dx, r: p.x + 40 + d.dx, t: p.y - 20 + d.dy, b: p.y + d.dy });
const overlap = (a: ReturnType<typeof rect>, b: ReturnType<typeof rect>) => a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t;

describe("etiquetas do escritório sem sobreposição", () => {
  it("placas distantes não se mexem", () => {
    expect(declutter([P("a", 0, 100), P("b", 300, 100)])).toEqual({ a: { dx: 0, dy: 0 }, b: { dx: 0, dy: 0 } });
  });
  it("placas de agente colididas ficam empilhadas sem sobreposição, inclusive em grupo, e só sobem", () => {
    const ps = [P("a", 0, 100), P("b", 10, 104), P("c", 20, 98), P("d", 30, 101)];
    const d = declutter(ps);
    const rs = ps.map((p) => rect(p, d[p.id]!));
    for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) expect(overlap(rs[i]!, rs[j]!)).toBe(false);
    expect(Object.values(d).every((x) => x.dy <= 0 && x.dx === 0)).toBe(true);
  });
  it("etiqueta sobre o corpo de um personagem sai de cima dele", () => {
    const body = { l: -15, r: 15, t: 90, b: 150 };
    const d = declutter([P("a", 0, 110)], [body]);
    expect(overlap(rect(P("a", 0, 110), d.a!), body)).toBe(false);
  });
  it("placa de sala fica perto da âncora: escolhe a 1ª posição próxima livre, ou fica no lugar", () => {
    const body = { l: -15, r: 15, t: 90, b: 150 };
    const sala = { ...P("sala", 0, 110), candidates: nearby(80, 20) };
    const d = declutter([sala], [body]).sala!;
    expect(overlap(rect(sala, d), body)).toBe(false);
    expect(Math.abs(d.dx) <= 36 && Math.abs(d.dy) <= 46).toBe(true);  // nunca vai para longe da sala
    const blocked = declutter([{ ...sala }], [{ l: -500, r: 500, t: -500, b: 500 }]).sala!;
    expect(blocked).toEqual({ dx: 0, dy: 0 });
  });
  it("a selecionada não sai do lugar; a outra cede", () => {
    const d = declutter([P("a", 0, 100), P("sel", 5, 100, platePriority(true, undefined, false))]);
    expect(d.sel).toEqual({ dx: 0, dy: 0 });
    expect(d.a!.dy).toBeLessThan(0);
  });
  it("estado crítico tem prioridade sobre ativo comum e sobre parado", () => {
    expect(platePriority(false, "crit", false)).toBeGreaterThan(platePriority(false, "warn", false));
    expect(platePriority(false, "warn", false)).toBeGreaterThan(platePriority(false, undefined, false));
    expect(platePriority(false, undefined, false)).toBeGreaterThan(platePriority(false, undefined, true));
  });
});
