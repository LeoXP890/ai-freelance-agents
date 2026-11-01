// Capturas de tela da UI real contra o stack DRY_RUN (SDD v3 §10 Fase 0 / §12 evidências).
// Uso: node scripts/capture_screens.mjs <before|after> [baseURL]
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const phase = process.argv[2] ?? "before";
const base = process.argv[3] ?? "http://localhost:8080";
const out = new URL(`../../../docs/desktop-migration/screenshots/${phase}/`, import.meta.url);
mkdirSync(out, { recursive: true });
const shot = (page, name) => page.screenshot({ path: new URL(`${name}.png`, out).pathname });

const login = async (email) => (await (await fetch(`${base}/api/v1/auth/login`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password: "simulado-123" }) })).json()).access_token;

const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const token = await login("operator@demo.local");
const shots = [];

for (const [w, h] of [[1440, 900], [1366, 768]]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  const tag = `${w}x${h}`;
  await page.goto(base);
  await page.waitForTimeout(800);
  await shot(page, `01-login-${tag}`); shots.push(`01-login-${tag}`);
  // permissões extras só para visualizar telas de admin (o backend continua recusando ações)
  await page.route("**/v1/auth/me", async (r) => {
    const me = await (await r.fetch()).json();
    await r.fulfill({ json: { ...me, permissions: [...new Set([...me.permissions, "users:manage", "audit:read"])] } });
  });
  await page.evaluate((t) => { sessionStorage.setItem("aic.token", t); localStorage.setItem("aic.view", "3d"); }, token);
  await page.reload();
  await page.locator(".tag").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(2500);
  await shot(page, `02-escritorio-3d-${tag}`); shots.push(`02-escritorio-3d-${tag}`);
  const agents = page.getByRole("navigation", { name: "Agentes do escritório" }).getByRole("button");
  const names = await agents.allTextContents();
  const open = async (name) => {
    const i = names.findIndex((n) => n.startsWith(name));
    await agents.nth(i).focus(); await page.keyboard.press("Enter"); await page.waitForTimeout(1500);
  };
  if (w === 1440) {
    for (const [n, label] of [["Atlas", "atlas-missoes"], ["Luna", "luna-oportunidades"], ["Nexus", "nexus-fornecedores"],
      ["Nova", "nova-anuncios"], ["Logan", "logan-pedidos"], ["Finn", "finn-financeiro"], ["Pixel", "pixel-campanhas"],
      ["Orion", "orion-atribuicao"], ["Clara", "clara-agente-generico"]]) {
      await open(n);
      await shot(page, `03-agente-${label}-${tag}`); shots.push(`03-agente-${label}-${tag}`);
      const panel = page.locator("aside.panel");
      await panel.evaluate((el) => { el.scrollTop = el.scrollHeight; });
      await shot(page, `03-agente-${label}-rolado-${tag}`); shots.push(`03-agente-${label}-rolado-${tag}`);
      await page.getByRole("button", { name: "Fechar painel" }).click().catch(() => {});
    }
    await page.getByRole("button", { name: /^Aprovações/ }).click(); await page.waitForTimeout(1000);
    await shot(page, `04-aprovacoes-${tag}`); shots.push(`04-aprovacoes-${tag}`);
    await page.getByRole("button", { name: "Usuários" }).click(); await page.waitForTimeout(1000);
    await shot(page, `05-usuarios-${tag}`); shots.push(`05-usuarios-${tag}`);
    await page.getByRole("button", { name: "Trocar senha" }).click(); await page.waitForTimeout(500);
    await shot(page, `06-trocar-senha-${tag}`); shots.push(`06-trocar-senha-${tag}`);
  }
  await page.getByRole("button", { name: "2D", exact: true }).click(); await page.waitForTimeout(1000);
  await shot(page, `07-escritorio-2d-${tag}`); shots.push(`07-escritorio-2d-${tag}`);
  await ctx.close();
}
console.log(JSON.stringify({ phase, count: shots.length }));
await browser.close();
