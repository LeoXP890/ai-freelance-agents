// Auditoria formal de contraste (UXD-005/023): lê os tokens REAIS do CSS do app (tema escuro e claro) e exige
// WCAG AA — texto ≥ 4,5:1 (inclui legendas de 12 px) e componentes/gráficos ≥ 3:1.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { contrast, REQUIRED, tokensFrom } from "../lib/contrast";

const css = readFileSync(new URL("../desktop/desk.css", import.meta.url), "utf8");
const dark = tokensFrom(css, ".desk");
const light = { ...dark, ...tokensFrom(css, '.desk[data-theme="light"]') };

describe("contraste WCAG dos tokens do app desktop", () => {
  it("cálculo confere com valores de referência", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });
  for (const [name, t] of [["escuro", dark], ["claro", light]] as const) {
    it(`tema ${name}: todos os pares exigidos passam`, () => {
      const fails = REQUIRED.filter(([f, b, min]) => !(t[f] && t[b]) || contrast(t[f]!, t[b]!) < min)
        .map(([f, b, min, what]) => `${what}: ${f} sobre ${b} = ${t[f] && t[b] ? contrast(t[f]!, t[b]!).toFixed(2) : "token ausente"} (mín. ${min})`);
      expect(fails).toEqual([]);
    });
  }
  it("o tema claro redefine todos os papéis de cor (não herda escuro por engano)", () => {
    const own = tokensFrom(css, '.desk[data-theme="light"]');
    for (const k of ["app-bg", "panel-bg", "text-main", "text-muted", "text-faint", "accent", "accent-ink", "critical", "warning", "success", "field-border", "demo-text"]) expect(own[k], k).toBeTruthy();
  });
});
