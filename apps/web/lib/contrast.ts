// Contraste WCAG 2.x (UXD-005/023): razão de luminância relativa entre duas cores sRGB hex.
function lin(c: number): number {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}
export function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((x) => x + x).join("") : h, 16);
  return 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
}
export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p) as [number, number];
  return (x + 0.05) / (y + 0.05);
}
/** Pares exigidos: texto ≥ 4,5:1 (AA, inclui texto de 12 px), componentes de interface e gráficos ≥ 3:1 (1.4.11). */
export const REQUIRED: [fg: string, bg: string, min: number, what: string][] = [
  ["text-main", "app-bg", 4.5, "texto principal no fundo"], ["text-main", "panel-bg", 4.5, "texto principal no cartão"],
  ["text-main", "panel-raised", 4.5, "texto principal em item selecionado"],
  ["text-muted", "app-bg", 4.5, "texto secundário no fundo"], ["text-muted", "panel-bg", 4.5, "texto secundário no cartão"],
  ["text-muted", "panel-raised", 4.5, "texto secundário em item selecionado"],
  ["text-faint", "app-bg", 4.5, "legenda no fundo"], ["text-faint", "panel-bg", 4.5, "legenda no cartão"],
  ["accent-ink", "accent", 4.5, "botão principal"],
  ["accent-2", "panel-bg", 4.5, "links e chips informativos"], ["success", "panel-bg", 4.5, "chip de sucesso"],
  ["warning", "panel-bg", 4.5, "chip de atenção"], ["critical", "panel-bg", 4.5, "chip/erro crítico"],
  ["critical", "app-bg", 4.5, "mensagem de erro"], ["demo-text", "app-bg", 4.5, "faixa de SIMULAÇÃO"],
  ["chart-1", "panel-bg", 3, "série dos gráficos"], ["focus-ring", "app-bg", 3, "anel de foco no fundo"],
  ["focus-ring", "panel-bg", 3, "anel de foco no cartão"], ["field-border", "panel-bg", 3, "borda de campo de formulário"],
  ["field-border", "app-bg", 3, "borda de campo no fundo"],
];

/** Lê os tokens de um bloco CSS (`--nome: #hex;`). */
export function tokensFrom(css: string, selector: string): Record<string, string> {
  const i = css.indexOf(`${selector} {`);
  if (i < 0) return {};
  const block = css.slice(i, css.indexOf("}", i));
  return Object.fromEntries([...block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\b/g)].map((m) => [m[1]!, m[2]!]));
}
