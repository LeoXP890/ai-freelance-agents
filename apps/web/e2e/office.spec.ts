import { readFileSync, writeFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

// Cena determinística: snapshot fixo + WebSocket simulado. O backend real só autentica e informa permissões.
const SNAPSHOT = JSON.parse(readFileSync(new URL("./fixtures/snapshot.json", import.meta.url), "utf8"));
const PENDING = [{ id: "p1" }, { id: "p2" }];

async function login(page: Page, email = "operator@demo.local") {
  const r = await page.request.post("/api/v1/auth/login", { data: { email, password: "simulado-123" } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).access_token as string;
}

async function openOffice(page: Page, view: "3d" | "2d", email?: string, snapshot = SNAPSHOT) {
  const token = await login(page, email);
  await page.route("**/v1/agent-presence/snapshot", (route) => route.fulfill({ json: snapshot }));
  await page.route("**/v1/approvals?status=PENDING_APPROVAL", (route) => route.fulfill({ json: { items: PENDING } }));
  await page.routeWebSocket(/\/v1\/office\/events/, (ws) => {
    ws.onMessage((m) => { if (JSON.parse(String(m)).type === "auth") ws.send(JSON.stringify({ type: "ready", as_of_seq: 100 })); });
  });
  await page.addInitScript(({ t, v }) => {
    sessionStorage.setItem("aic.token", t);
    localStorage.setItem("aic.view", v);
  }, { t: token, v: view });
  await page.goto("/?ui=legacy");
  await expect(page.getByText("Tempo real (WebSocket)")).toBeVisible();
}

test("3D: cena isométrica igual à referência", async ({ page }) => {
  await openOffice(page, "3d");
  await expect(page.locator(".tag")).toHaveCount(13);
  await expect(page.locator(".ceo-badge")).toHaveText("2 aprovações");
  await page.waitForTimeout(1500); // primeiro quadro com sombras e rótulos posicionados
  await expect(page.locator(".scene")).toHaveScreenshot("office-3d.png");
});

test("3D: rótulos de agentes e salas não se sobrepõem", async ({ page }) => {
  await openOffice(page, "3d");
  await expect(page.locator(".tag")).toHaveCount(13);
  const boxes = await page.locator(".tag, .room-label, .ceo-badge")
    .evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON()));
  expect(boxes).toHaveLength(13 + 9 + 1);
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const [a, b] = [boxes[i], boxes[j]];
      const overlap = a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
      expect(overlap, `rótulos ${i} e ${j} se sobrepõem`).toBeFalsy();
    }
});

test("2D: grade acessível igual à referência", async ({ page }) => {
  await openOffice(page, "2d");
  await expect(page.getByRole("list", { name: "Salas do escritório" }).locator(":scope > [role=listitem]")).toHaveCount(9);
  await expect(page.locator(".scene")).toHaveScreenshot("office-2d.png");
});

test("teclado: seleciona agente na vista 3D e abre o painel", async ({ page }) => {
  await openOffice(page, "3d");
  const nav = page.getByRole("navigation", { name: "Agentes do escritório" });
  await nav.getByRole("button").first().focus();
  await expect(nav).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("complementary", { name: /Painel do agente/ })).toBeVisible();
  await page.getByRole("button", { name: "Fechar painel" }).click();
  await page.getByRole("button", { name: /Caixa de aprovações da sala do CEO: 2 pendentes/ }).press("Enter");
  await expect(page.getByRole("complementary", { name: /aprova/i })).toBeVisible();
});

test("acessibilidade: controles nomeados e foco visível", async ({ page }) => {
  await openOffice(page, "3d");
  const unnamed = await page.locator("button, select, input, a[href]").evaluateAll((els) =>
    els.filter((e) => !(e.getAttribute("aria-label") || e.textContent?.trim() || e.getAttribute("title")
      || (e.id && document.querySelector(`label[for="${e.id}"]`)) || e.closest("label"))).map((e) => e.outerHTML.slice(0, 80)));
  expect(unnamed).toEqual([]);
  await expect(page.locator("canvas").locator("..").locator("..")).toBeVisible();
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    return cs.outlineStyle !== "none" || cs.boxShadow !== "none";
  });
  expect(outline).toBe(true);
});

test("celular: vista 2D sem rolagem horizontal", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openOffice(page, "2d");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("desempenho: orçamento de carga, draw calls e triângulos da cena 3D", async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  // buffer de Resource Timing padrão é 250 entradas: chamadas da API o enchem antes dos .glb
  await page.addInitScript(() => { localStorage.setItem("aic.debug", "1"); performance.setResourceTimingBufferSize(2000); });
  const t0 = Date.now();
  await openOffice(page, "3d");
  await expect(page.locator(".tag")).toHaveCount(13);
  const firstScene = Date.now() - t0;
  const countGlb = () => page.evaluate(() => performance.getEntriesByType("resource").filter((e) => e.name.includes(".glb")).length);
  await expect.poll(countGlb, { timeout: 15_000 }).toBeGreaterThanOrEqual(5); // modelos carregam depois dos rótulos (Suspense)
  const glbs = await countGlb();
  await page.waitForTimeout(1500);
  const frame = async () => page.evaluate(() => new Promise<{ calls: number; triangles: number }>((res) => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const r = (window as unknown as { __aicGl: { info: { render: { calls: number; triangles: number } } } }).__aicGl.info.render;
      res({ calls: r.calls, triangles: r.triangles });
    }));
  }));
  const high = await frame();
  await page.getByLabel("Qualidade gráfica").selectOption("low");
  await page.waitForTimeout(500);
  const low = await frame();
  const fps = await page.evaluate(() => new Promise<number>((res) => {
    let n = 0; const start = performance.now();
    const tick = () => { n++; if (performance.now() - start < 2000) requestAnimationFrame(tick); else res(n / ((performance.now() - start) / 1000)); };
    requestAnimationFrame(tick);
  }));
  const js = await page.evaluate(() => performance.getEntriesByType("resource")
    .filter((e) => e.name.endsWith(".js")).reduce((s, e) => s + ((e as PerformanceResourceTiming).transferSize || 0), 0));
  const report = {
    first_scene_ms: firstScene, glb_requests: glbs, js_transfer_bytes: js,
    high: { draw_calls: high.calls, triangles: high.triangles }, low: { draw_calls: low.calls, triangles: low.triangles },
    fps_swiftshader_low_informativo: Math.round(fps * 10) / 10,
    note: "draw calls/triângulos por quadro são determinísticos (orçamento); FPS em SwiftShader varia com a carga da máquina",
  };
  writeFileSync(new URL("../../../progress/test-reports/office_perf.json", import.meta.url), JSON.stringify(report, null, 2));
  info.annotations.push({ type: "perf", description: JSON.stringify(report) });
  expect(firstScene).toBeLessThan(8000);
  expect(glbs).toBe(5);  // um download por modelo, compartilhado pelas 9 salas
  expect(high.calls).toBeLessThan(250);
  expect(high.triangles).toBeLessThan(60_000);
  expect(low.calls).toBeLessThan(high.calls);
});

const CAMPAIGNS = JSON.parse(readFileSync(new URL("./fixtures/campaigns.json", import.meta.url), "utf8"));
const STOP_REPORT = JSON.parse(readFileSync(new URL("./fixtures/stop_report.json", import.meta.url), "utf8"));

async function openAgent(page: Page, index: number) {
  const b = page.getByRole("navigation", { name: "Agentes do escritório" }).getByRole("button").nth(index);
  await b.focus();
  await page.keyboard.press("Enter");
}

test("mídia: campanhas do Pixel e atribuição do Orion", async ({ page }) => {
  await page.route("**/v1/campaigns", (route) => route.fulfill({ json: CAMPAIGNS }));
  await openOffice(page, "3d");
  const order: string[] = SNAPSHOT.agents.map((a: { agent_id: string }) => a.agent_id);
  await openAgent(page, order.indexOf("pixel"));
  const panel = page.getByRole("complementary", { name: /Painel do agente Pixel/ });
  await expect(panel.locator(".campaign")).toHaveCount(3);
  await expect(panel.getByText("divergente do ledger")).toBeVisible();
  await expect(panel.getByText("Teto atingido")).toBeVisible();
  await expect(panel.locator(".hb-bar.over")).toHaveCount(1);
  await panel.screenshot({ path: "test-results/pixel-campaigns.png" });
  await page.getByRole("button", { name: "Fechar painel" }).click();
  await openAgent(page, order.indexOf("orion"));
  const orion = page.getByRole("complementary", { name: /Painel do agente Orion/ });
  await expect(orion.getByRole("list", { name: "ROAS por campanha" }).locator("li")).toHaveCount(3);
  await expect(orion.getByText("ROAS geral")).toBeVisible();
  const attribution = orion.locator(".dept").filter({ hasText: "Atribuição de mídia" });
  await attribution.getByRole("button", { name: "Ver tabela" }).click();
  await expect(attribution.locator("table.data tbody tr")).toHaveCount(3);
  await orion.screenshot({ path: "test-results/orion-attribution.png" });
});

test("kill switch: sem permissão a faixa não oferece o relatório", async ({ page }) => {
  await openOffice(page, "2d", undefined, { ...SNAPSHOT, emergency_stop: true });
  const banner = page.getByRole("alert").filter({ hasText: "EMERGENCY STOP ATIVO" });
  await expect(banner).toBeVisible();
  await expect(banner.getByRole("button", { name: "Ver relatório" })).toHaveCount(0);
});

test("kill switch: faixa leva ao relatório com resultado real das pausas", async ({ page }) => {
  await page.route("**/v1/agent-presence/snapshot", (route) =>
    route.fulfill({ json: { ...SNAPSHOT, emergency_stop: true } }));
  await page.route("**/v1/emergency-stop/report", (route) => route.fulfill({ json: STOP_REPORT }));
  await page.route("**/v1/auth/me", async (route) => {  // UI: botão só para quem pode ver o relatório
    const me = await (await route.fetch()).json();
    await route.fulfill({ json: { ...me, permissions: [...me.permissions, "audit:read"] } });
  });
  const token = await login(page);
  await page.route("**/v1/approvals?status=PENDING_APPROVAL", (route) => route.fulfill({ json: { items: PENDING } }));
  await page.routeWebSocket(/\/v1\/office\/events/, (ws) => {
    ws.onMessage((m) => { if (JSON.parse(String(m)).type === "auth") ws.send(JSON.stringify({ type: "ready", as_of_seq: 100 })); });
  });
  await page.addInitScript(({ t }) => { sessionStorage.setItem("aic.token", t); localStorage.setItem("aic.view", "2d"); }, { t: token });
  await page.goto("/?ui=legacy");
  const banner = page.getByRole("alert").filter({ hasText: "EMERGENCY STOP ATIVO" });
  await expect(banner).toBeVisible();
  await banner.getByRole("button", { name: "Ver relatório" }).click();
  const rep = page.getByRole("complementary", { name: "Relatório do kill switch" });
  await expect(rep.getByText("Pausas externas (1/3 confirmadas)")).toBeVisible();
  await expect(rep.getByText("CONFIRMADA", { exact: true })).toBeVisible();
  await expect(rep.getByText("FALHOU", { exact: true })).toBeVisible();
  await expect(rep.getByText(/aguarda aprovação na sala do CEO/)).toBeVisible();
  await rep.screenshot({ path: "test-results/stop-report.png" });
});

test("rede: queda do WebSocket → novo snapshot, fallback HTTP e nenhum evento perdido", async ({ page }) => {
  const token = await login(page);
  let snapshots = 0;
  let conns = 0;
  let polled = 0;
  let first: { close(o: { code: number; reason?: string }): Promise<void> } | null = null;
  const luna = SNAPSHOT.agents.find((a: { agent_id: string }) => a.agent_id === "luna");
  const ev = {
    schema_version: 1, seq: 101, event_id: "0f6c2a8e-5a0b-4c3e-9d1e-000000000101", type: "agent.task.state_changed",
    agent_id: "luna", task_id: null, state: "TOOL_CALL", timestamp: "2026-10-24T12:00:00Z", mode: "DRY_RUN",
    simulated: true, payload: {},
  };
  await page.route("**/v1/agent-presence/snapshot", (route) => { snapshots++; return route.fulfill({ json: SNAPSHOT }); });
  await page.route("**/v1/approvals?status=PENDING_APPROVAL", (route) => route.fulfill({ json: { items: PENDING } }));
  await page.route("**/v1/office/events?after_seq=*", (route) => {
    polled++;
    const after = Number(new URL(route.request().url()).searchParams.get("after_seq"));
    return route.fulfill({ json: { items: after < 101 ? [ev] : [], next_after_seq: 101 } });
  });
  await page.routeWebSocket(/\/v1\/office\/events/, (ws) => {
    conns++;
    if (conns === 1) {  // 1ª conexão: fica pronta; o servidor cai quando o teste mandar (determinístico)
      first = ws;
      ws.onMessage((m) => {
        if (JSON.parse(String(m)).type === "auth") ws.send(JSON.stringify({ type: "ready", as_of_seq: 100 }));
      });
    } else {
      ws.close({ code: 1006 });  // rede instável: novas tentativas caem antes de ficar prontas
    }
  });
  await page.addInitScript(({ t }) => { sessionStorage.setItem("aic.token", t); localStorage.setItem("aic.view", "2d"); }, { t: token });
  await page.goto("/?ui=legacy");
  expect(luna.state).not.toBe("TOOL_CALL");
  await expect(page.getByText("Tempo real (WebSocket)")).toBeVisible();
  await first!.close({ code: 1011, reason: "queda simulada" });
  await expect(page.getByText("Tempo real (HTTP)")).toBeVisible({ timeout: 20_000 });
  expect(conns).toBeGreaterThanOrEqual(4);  // 1 queda + 3 falhas antes do fallback
  expect(snapshots).toBeGreaterThanOrEqual(2);  // cada reconexão busca snapshot novo (estado consistente)
  const card = page.getByRole("listitem").filter({ hasText: "Luna" }).first();
  await expect(card).toContainText("Chamando ferramenta");  // evento entregue pelo polling, a partir do cursor
  expect(polled).toBeGreaterThanOrEqual(1);
  await expect(page.getByText("seq 101")).toBeVisible();
});
