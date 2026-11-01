import { expect, test } from "@playwright/test";
const OUT = new URL("../../../../docs/desktop-migration/screenshots/final/", import.meta.url).pathname;
const ROOMS = ["Sala do CEO", "Laboratório de Pesquisa", "Estúdio de Marketing", "Comércio & Fornecedores", "Sala de Reuniões",
  "Finanças & Compliance", "Operações & Atendimento"];
test("salas do escritório", async ({ page }) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const tok = (await (await page.request.post("/api/v1/auth/login", { data: { email: "operator@demo.local", password: "simulado-123" } })).json()).access_token;
  await page.addInitScript((t) => { sessionStorage.setItem("aic.token", t); localStorage.setItem("aic.desk.page", "office"); localStorage.setItem("aic.theme", "dark"); }, tok);
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `${OUT}office-00-sede.png` });
  for (const [i, r] of ROOMS.entries()) {
    await page.getByRole("button", { name: r, exact: true }).first().click();
    await page.waitForTimeout(2200);
    await page.screenshot({ path: `${OUT}office-0${i + 1}-${r.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^\w]+/g, "-").toLowerCase()}.png` });
  }
});
