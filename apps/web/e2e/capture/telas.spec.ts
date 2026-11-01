// Captura de revisão visual (não faz parte da suíte): 17 telas × tema escuro/claro, proprietário com MFA real.
import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { expect, test } from "@playwright/test";

const ROOT = new URL("../../../../", import.meta.url).pathname;
const OUT = new URL("../../../../docs/desktop-migration/screenshots/final/", import.meta.url).pathname;
const COMPOSE = ["compose", "-f", "infra/compose/docker-compose.yml", "--env-file", "infra/env/dry_run.env"];
const ALL = ["command", "office", "approvals", "agents", "chat", "missions", "products", "suppliers", "channels",
  "campaigns", "orders", "finance", "reports", "knowledge", "gates", "settings"]; // auditoria: capturada pelo E2E filtrada (sem contas reais)
const ONLY = (process.env.REVIEW_PAGES ?? "").split(",").filter(Boolean);
const PAGES = ALL;

function totp(secret: string, at = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(Math.floor(at / 30_000)));
  const h = createHmac("sha1", key).update(msg).digest();
  const o = h[h.length - 1]! & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

test("revisão visual — todas as telas", async ({ page }) => {
  test.setTimeout(600_000);
  const email = `revisao-${Date.now()}@e2e.local`, password = "senha-revisao-visual-123";
  execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-m", "aicommerce.manage", "create-user",
    "--email", email, "--name", "Proprietário (revisão)", "--role", "owner", "--password-stdin"], { cwd: ROOT, input: `${password}\n` });
  try {
    const first = await (await page.request.post("/api/v1/auth/login", { data: { email, password } })).json();
    const enroll = await (await page.request.post("/api/v1/auth/mfa/enroll/start", { data: { enrollment_token: first.enrollment_token } })).json();
    const tok = (await (await page.request.post("/api/v1/auth/mfa/enroll/confirm",
      { data: { enrollment_token: first.enrollment_token, code: totp(enroll.secret) } })).json()).access_token as string;
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const theme of ["dark", "light"]) {
      for (const p of PAGES.filter((x) => !ONLY.length || ONLY.includes(x))) {
        await page.addInitScript(({ t, pg, th }) => {
          sessionStorage.setItem("aic.token", t); localStorage.setItem("aic.desk.page", pg); localStorage.setItem("aic.theme", th);
        }, { t: tok, pg: p, th: theme });
        await page.goto("/");
        await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();
        await page.waitForTimeout(p === "office" ? 3500 : 1500);
        if (p === "settings") await page.getByRole("heading", { name: "Usuários" }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${OUT}${theme}-${String(PAGES.indexOf(p) + 1).padStart(2, "0")}-${p}.png` });
      }
    }
  } finally {
    execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-m", "aicommerce.manage", "deactivate-user", "--email", email], { cwd: ROOT });
  }
});
