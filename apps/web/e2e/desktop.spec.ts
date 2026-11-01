// App desktop (SDD v3 §12 cenários E2E) contra o stack DRY_RUN: login real, presença com snapshot fixo +
// WebSocket simulado (cena determinística), demais dados reais da API.
import { readFileSync, writeFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

const SNAPSHOT = JSON.parse(readFileSync(new URL("./fixtures/snapshot.json", import.meta.url), "utf8"));
const AFTER = new URL("../../../docs/desktop-migration/screenshots/after/", import.meta.url);

async function token(page: Page, email = "operator@demo.local") {
  const r = await page.request.post("/api/v1/auth/login", { data: { email, password: "simulado-123" } });
  return (await r.json()).access_token as string;
}

async function open(page: Page, pageId = "command", snapshot = SNAPSHOT) {
  const t = await token(page);
  await page.route("**/v1/agent-presence/snapshot", (r) => r.fulfill({ json: snapshot }));
  await page.routeWebSocket(/\/v1\/office\/events/, (ws) => {
    ws.onMessage((m) => { if (JSON.parse(String(m)).type === "auth") ws.send(JSON.stringify({ type: "ready", as_of_seq: 100 })); });
  });
  await page.addInitScript(({ tok, p }) => {
    sessionStorage.setItem("aic.token", tok);
    localStorage.setItem("aic.desk.page", p);
  }, { tok: t, p: pageId });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();
}

test("1 · login → Centro de comando mostra modo e conexão", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("E-mail").fill("operator@demo.local");
  await page.getByLabel("Senha").fill("simulado-123");
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/Bom dia|Boa tarde|Boa noite/);
  await expect(page.locator(".side-foot").getByText("SIMULAÇÃO")).toBeVisible();
  await expect(page.locator(".side-foot").getByRole("status")).toContainText(/Conectado/);
  await expect(page.getByText(/SIMULAÇÃO \(DRY_RUN\)/)).toBeVisible();
  for (const k of ["Pedidos pagos", "Receita líquida", "IA no mês", "Alertas críticos"]) await expect(page.getByText(k)).toBeVisible();
  await expect(page.getByText("Carregando…")).toHaveCount(0, { timeout: 10_000 });
  await page.screenshot({ path: new URL("01-centro-de-comando-1440x900.png", AFTER).pathname });
});

test("2 · diretório → Luna → tarefa real → workspace com abas", async ({ page }) => {
  await open(page, "agents");
  await expect(page.locator(".agent-card")).toHaveCount(13);
  await page.getByRole("searchbox").or(page.getByPlaceholder("Buscar por nome, função ou área")).fill("luna");
  await expect(page.locator(".agent-card")).toHaveCount(1);
  await page.getByRole("button", { name: "Abrir agente Luna" }).click();
  const drawer = page.getByRole("complementary", { name: "Agente Luna" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("Pesquisa mercados e tendências")).toBeVisible();
  await drawer.getByRole("tab", { name: "Tarefas" }).click();
  await expect(drawer.getByRole("tab", { name: "Tarefas" })).toHaveAttribute("aria-selected", "true");
  await page.screenshot({ path: new URL("02-agente-luna-tarefas-1440x900.png", AFTER).pathname });
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await page.getByPlaceholder("Buscar por nome, função ou área").fill("");
  await page.screenshot({ path: new URL("03-diretorio-agentes-1440x900.png", AFTER).pathname });
});

test("4 · sede 3D completa → sala destacada → agente selecionado → drawer sem perder a cena", async ({ page }) => {
  await open(page, "office");
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.locator("[data-ready='1']")).toHaveCount(1, { timeout: 15_000 });
  const rooms = page.getByRole("navigation", { name: "Salas do escritório" }).getByRole("button");
  await expect(rooms).toHaveCount(7);
  // vista geral: nomes só de quem está em atividade (sem inundar a cena de texto)
  const active = SNAPSHOT.agents.filter((a: { state: string }) => !["IDLE", "COMPLETED"].includes(a.state)).length;
  await expect(page.locator(".nameplate")).toHaveCount(active);
  await expect(page.getByRole("navigation", { name: "Agentes no escritório" }).getByRole("button", { name: /:/ })).toHaveCount(13);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: new URL("04-escritorio-sede-completa-1440x900.png", AFTER).pathname });
  // sala destacada: foco + painel do departamento com os agentes reais da sala
  await rooms.filter({ hasText: "Laboratório de Pesquisa" }).click();
  const panel = page.getByRole("complementary", { name: "Sala: Laboratório de Pesquisa" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("button", { name: /Luna/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /Vera/ })).toBeVisible();
  await expect(page.locator(".nameplate").filter({ hasText: "Luna" })).toHaveCount(1);
  await page.waitForTimeout(2200);
  await page.screenshot({ path: new URL("05-escritorio-sala-destacada-1440x900.png", AFTER).pathname });
  // agente selecionado pela lista: drawer + realce na cena
  await page.getByRole("navigation", { name: "Agentes no escritório" }).getByRole("button", { name: /^Vera:/ }).click();
  await expect(page.getByRole("complementary", { name: "Agente Vera" })).toBeVisible();
  await expect(page.locator(".nameplate.sel")).toHaveCount(1);
  await page.waitForTimeout(800);
  await page.screenshot({ path: new URL("05b-escritorio-agente-selecionado-1440x900.png", AFTER).pathname });
  await page.getByRole("button", { name: "Fechar agente" }).click();
  await expect(page.locator("canvas")).toBeVisible();
  await page.keyboard.press("r");
  await expect(panel).toBeHidden();
});

test("4b · estado do backend muda o rótulo da placa e a lista (determinístico, sem aleatoriedade)", async ({ page }) => {
  const snap = structuredClone(SNAPSHOT);
  for (const a of snap.agents) if (a.agent_id === "luna") a.state = "WAITING_APPROVAL";
  await open(page, "office", snap);
  await expect(page.locator(".nameplate").filter({ hasText: "Luna" })).toContainText("Aguardando aprovação");
  await expect(page.getByRole("navigation", { name: "Agentes no escritório" }).getByRole("button", { name: "Luna: Aguardando aprovação" })).toBeVisible();
});

test("4c · agente movido pelo backend para outra sala aparece lá", async ({ page }) => {
  const snap = structuredClone(SNAPSHOT);
  for (const a of snap.agents) if (a.agent_id === "luna") a.room = "meeting";
  await open(page, "office", snap);
  await page.getByRole("navigation", { name: "Salas do escritório" }).getByRole("button", { name: "Sala de Reuniões" }).click();
  await expect(page.getByRole("complementary", { name: "Sala: Sala de Reuniões" }).getByRole("button", { name: /Luna/ })).toBeVisible();
});

test("paleta Ctrl/⌘+K e atalhos de navegação", async ({ page }) => {
  await open(page, "command");
  await page.keyboard.press("ControlOrMeta+k");
  const pal = page.getByRole("dialog", { name: "Pesquisar e navegar" });
  await expect(pal).toBeVisible();
  await pal.getByRole("textbox").fill("finn");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("complementary", { name: "Agente Finn" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("ControlOrMeta+3");
  await expect(page.getByRole("heading", { level: 1, name: "Agentes" })).toBeVisible();
  await page.keyboard.press("ControlOrMeta+2");
  await expect(page.locator("canvas")).toBeVisible();
});

test("todas as páginas da navegação abrem sem erro", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await open(page, "command");
  const items = page.getByRole("navigation", { name: "Principal" }).getByRole("button");
  // itens com permissão só aparecem depois de /auth/me: espera o menu completo antes de contar
  await expect.poll(() => items.count(), { timeout: 10_000 }).toBeGreaterThanOrEqual(14);
  const n = await items.count();
  for (let i = 0; i < n; i++) {
    const label = (await items.nth(i).innerText()).split("\n")[0]!.trim();
    await items.nth(i).click();
    await expect(page.locator(".crumbs strong")).toHaveText(label);
    await page.waitForTimeout(300);
  }
  expect(n).toBeGreaterThanOrEqual(14);
  expect(errors).toEqual([]);
});

test("1366×768 sem rolagem horizontal; foco visível; rollback ?ui=legacy", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await open(page, "command");
  await expect(page.getByText("Carregando…")).toHaveCount(0, { timeout: 10_000 });
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
  await page.screenshot({ path: new URL("06-centro-de-comando-1366x768.png", AFTER).pathname });
  await page.keyboard.press("Tab");
  const ring = await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(ring).not.toBe("none");
  await page.goto("/?ui=legacy");
  await expect(page.getByRole("heading", { name: "Escritório" })).toBeVisible();
});

test("desempenho da sede completa: draw calls, triângulos, FPS e carga nas 4 qualidades (Auto/Alta/Equilibrada/Baixa)", async ({ page }) => {
  test.setTimeout(480_000);
  const out: Record<string, unknown> = { renderer: "SwiftShader (CPU, headless) — pior caso; GPU real medida no app", animations: "on" };
  await page.emulateMedia({ reducedMotion: "no-preference" }); // animações ligadas: renderiza todo quadro
  for (const q of ["auto", "high", "balanced", "low"]) {
    await page.addInitScript((qq) => { localStorage.setItem("aic.debug", "1"); localStorage.setItem("aic.quality", qq); }, q);
    const t0 = Date.now();
    await open(page, "office");
    await expect(page.locator("[data-ready='1']")).toHaveCount(1, { timeout: 90_000 });
    const loadMs = Date.now() - t0;  // da navegação até a cena pronta (enquadrada)
    await page.waitForTimeout(1500);
    const r = await page.evaluate((secs) => new Promise<{ calls: number; triangles: number; fps: number; heapMb: number | null }>((res) => {
      const gl = (window as unknown as { __aicGl: { info: { render: { calls: number; triangles: number } } } }).__aicGl;
      let frames = 0; const t0 = performance.now();
      const tick = () => {
        frames += 1;
        if (performance.now() - t0 < secs * 1000) { requestAnimationFrame(tick); return; }
        const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
        res({ calls: gl.info.render.calls, triangles: gl.info.render.triangles, fps: Math.round((frames / ((performance.now() - t0) / 1000)) * 10) / 10,
          heapMb: mem ? Math.round(mem.usedJSHeapSize / 1e6) : null });
      };
      requestAnimationFrame(tick);
    }), q === "low" ? 30 : 8);
    out[q] = { ...r, loadMs };
    // sede inteira (7 salas, 13 agentes); estático "assado" (Baked) em poucas malhas
    expect(r.calls).toBeLessThan(q === "low" ? 220 : 300); // ~180: estático assado + 13 avatares × 5 partes animadas
    expect(r.triangles).toBeLessThan(q === "low" ? 200_000 : 350_000);
    // carga em SwiftShader (CPU) é informativa como o FPS: no CI levou ~26 s (run 40209979649); o limite só pega travamento
    expect(loadMs).toBeLessThan(90_000);
    expect(r.fps).toBeGreaterThan(0); // FPS em SwiftShader (CPU) é só informativo — critério é o app real na GPU
  }
  writeFileSync(new URL("../../../progress/test-reports/office_hq_perf.json", import.meta.url), JSON.stringify(out, null, 2));
});

test("A12 · foco do teclado fica preso no diálogo aberto (paleta)", async ({ page }) => {
  await open(page, "command");
  await page.keyboard.press("ControlOrMeta+k");
  const pal = page.getByRole("dialog", { name: "Pesquisar e navegar" });
  await expect(pal).toBeVisible();
  for (const key of ["Shift+Tab", "Shift+Tab", "Shift+Tab", "Tab", "Tab", "Tab", "Tab", "Tab"]) {
    await page.keyboard.press(key);
    expect(await pal.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  }
});

for (const motion of ["reduce", "no-preference"] as const) test(`A19 · evento do backend muda a POSE real do personagem (braços e cabeça na cena 3D) · movimento ${motion}`, async ({ page }) => {
  test.setTimeout(60_000);
  await page.emulateMedia({ reducedMotion: motion }); // "reduce" desenha sob demanda: a pose precisa chegar ao alvo mesmo assim
  const t = await token(page);
  await page.route("**/v1/agent-presence/snapshot", (r) => r.fulfill({ json: SNAPSHOT })); // Luna começa IDLE
  let push: (state: string) => void = () => undefined;
  let connected!: () => void;
  const ready = new Promise<void>((r) => { connected = r; });
  await page.routeWebSocket(/\/v1\/office\/events/, (ws) => {
    let seq = 100;
    push = (state) => ws.send(JSON.stringify({ type: "event", event: { schema_version: 1, seq: ++seq, event_id: `e2e-${seq}`,
      type: "agent.state.changed", agent_id: "luna", task_id: null, state, timestamp: new Date().toISOString(),
      mode: "DRY_RUN", simulated: true, payload: {} } }));
    ws.onMessage((m) => {
      if (JSON.parse(String(m)).type === "auth") { ws.send(JSON.stringify({ type: "ready", as_of_seq: 100 })); connected(); }
    });
  });
  await page.addInitScript((tok) => {
    sessionStorage.setItem("aic.token", tok);
    localStorage.setItem("aic.desk.page", "office");
    localStorage.setItem("aic.debug", "1"); // expõe o grafo da cena (só leitura) para inspecionar os membros
  }, t);
  await page.goto("/");
  const limbs = () => page.evaluate(() => {
    type Obj = { userData: { pose?: string }; rotation: { x: number }; getObjectByName(n: string): Obj | undefined };
    const scene = (window as unknown as { __aicScene?: Obj }).__aicScene;
    const luna = scene?.getObjectByName("agent:luna");
    if (!luna) return null;
    const x = (n: string) => luna.getObjectByName(n)!.rotation.x;
    return { pose: luna.userData.pose, armL: x("armL"), armR: x("armR"), head: x("head") };
  });
  const until = (ok: (l: NonNullable<Awaited<ReturnType<typeof limbs>>>) => boolean) =>
    expect.poll(async () => { const l = await limbs(); return !!l && ok(l); }, { timeout: 15_000 }).toBe(true);

  await until((l) => l.pose === "idle" && Math.abs(l.armL) < 0.2 && Math.abs(l.armR) < 0.2);
  await ready; // só envia eventos depois que o app autenticou no WebSocket
  push("WORKING"); // digitando: os dois braços à frente e a cabeça baixa
  await until((l) => l.pose === "working" && l.armL < -0.9 && l.armR < -0.9 && l.head > 0.15);
  push("PLANNING"); // mão no queixo: só o braço direito sobe bem alto
  await until((l) => l.pose === "planning" && l.armR < -1.8 && Math.abs(l.armL) < 0.3);
  push("WAITING_APPROVAL"); // segura o pedido levantado e olha para cima
  await until((l) => l.pose === "awaiting" && l.armR < -0.8 && l.head < -0.1);
  push("BLOCKED"); // cabisbaixo, braços colados
  await until((l) => l.pose === "blocked" && l.head > 0.3 && Math.abs(l.armL) < 0.2 && Math.abs(l.armR) < 0.2);
  push("ERROR"); // mãos na cabeça
  await until((l) => l.pose === "error" && l.armL < -2 && l.armR < -2);
  push("COMPLETED"); // satisfeito: cabeça erguida, braço aberto
  await until((l) => l.pose === "done" && l.head < -0.05 && l.armR < -0.2 && l.armR > -0.6);
  push("IDLE");
  await until((l) => l.pose === "idle" && Math.abs(l.armL) < 0.2 && Math.abs(l.armR) < 0.2);
});

test("páginas longas rolam com a roda do mouse até o fim (o conteúdo não fica cortado)", async ({ page }) => {
  for (const id of ["settings", "finance"]) {
    await open(page, id);
    await page.waitForTimeout(800);
    const pg = page.locator(".desk main > .page");
    const [client, scroll] = await pg.evaluate((el) => [el.clientHeight, el.scrollHeight]);
    if (scroll <= client) continue;  // cabe na tela: nada a rolar
    await page.mouse.move(800, 600);
    for (let i = 0; i < 8; i++) await page.mouse.wheel(0, 800);
    await expect.poll(() => pg.evaluate((el) => Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight - 2)).toBe(true);
    // o cartão mais baixo da página fica inteiro dentro da janela (antes ficava cortado e inalcançável)
    const lowest = await pg.evaluate((el) => Math.max(...[...el.querySelectorAll(".dk-card")].map((c) => c.getBoundingClientRect().bottom)));
    expect(lowest).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight));
  }
});

