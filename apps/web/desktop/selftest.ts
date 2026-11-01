// Autoteste do app no runtime desktop real (UXD-003/ADR-DESK-001): roda o app de verdade dentro do WKWebView/
// WebView2 e reporta o que renderizou. Só envia para 127.0.0.1 (nunca para a internet) e só lê estado visual.
// Uso: abrir index.html?selftest=http://127.0.0.1:<porta>
const KEY = "aic.selftest";

function target(): string | null {
  const t = new URLSearchParams(window.location.search).get("selftest") ?? sessionStorage.getItem(KEY);
  return t && /^http:\/\/127\.0\.0\.1:\d+$/.test(t) ? t : null;
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function until(fn: () => boolean, ms = 15000) {
  const t0 = performance.now();
  while (!fn() && performance.now() - t0 < ms) await wait(200);
  return fn();
}
const text = (sel: string) => document.querySelector(sel)?.textContent?.trim() ?? null;

/** Modo "device": prova o computador lembrado no app real (Keychain) e limpa tudo no fim. */
async function runDeviceSelfTest(base: string): Promise<void> {
  const { rememberDevice, forgetDevice, hasRememberedDevice } = await import("./device");
  const { api } = await import("@/lib/api");
  const phase = sessionStorage.getItem(`${KEY}.dphase`) ?? "remember";
  if (phase === "remember") {
    const tok = (await (await fetch(`${base}/token`)).text()).trim();
    await rememberDevice(tok);
    const stored = await hasRememberedDevice();
    await post(base, { phase: "device-remember", stored_in_keychain: stored });
    sessionStorage.setItem(`${KEY}.dphase`, "auto");
    sessionStorage.removeItem("aic.token"); // simula reabrir o app: sem sessão
    window.location.reload();
    return;
  }
  // reaberto sem sessão: o app deve entrar sozinho pelo Keychain, sem tela de login
  const signed = await until(() => !!document.querySelector('nav[aria-label="Principal"]'), 15000);
  const loginShown = !!document.querySelector('input[type="password"]');
  const tok = sessionStorage.getItem("aic.token");
  let revoked = false;
  if (tok) {
    const mine = await api.devices(tok).catch(() => ({ items: [] as { id: string; revoked: boolean; name: string }[] }));
    for (const d of mine.items.filter((x) => !x.revoked)) { await api.revokeDevice(tok, d.id).catch(() => undefined); revoked = true; }
  }
  await forgetDevice();
  const left = await hasRememberedDevice();
  sessionStorage.removeItem(`${KEY}.dphase`);
  await post(base, { phase: "device-auto", auto_signed_in: signed, login_screen_shown: loginShown,
    cleanup_revoked_on_server: revoked, keychain_cleaned: !left });
}

/** Modo "window" (cenário E2E 8): minimizar/restaurar e fechar→bandeja→reabrir; o servidor continua trabalhando
 *  enquanto a janela está fora de vista e a tela reidrata ao voltar. Só roda no build de teste (permissão de janela). */
async function runWindowSelfTest(base: string): Promise<void> {
  if (!sessionStorage.getItem("aic.token")) {
    sessionStorage.setItem("aic.token", (await (await fetch(`${base}/token`)).text()).trim());
    localStorage.setItem("aic.desk.page", "agents");
    window.location.reload();
    return;
  }
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  const { api } = await import("@/lib/api");
  const w = getCurrentWindow();
  const report: Record<string, unknown> = { phase: "window" };
  const connected = () => /Conectado/.test(text(".side-foot [role=status]") ?? "");
  report.connected_before = await until(connected);
  const seq = () => Number(document.querySelector("[data-last-seq]")?.getAttribute("data-last-seq") ?? 0);
  const tok = sessionStorage.getItem("aic.token")!;
  // 1) minimizar: o servidor executa uma tarefa enquanto a janela está minimizada
  const s0 = seq();
  await w.minimize();
  const t1 = await api.createTask(tok, "luna", "research.mock", { topic: "autoteste janela minimizada" });
  await wait(4000);
  report.minimized_seen = await w.isMinimized();
  await w.unminimize(); await w.setFocus();
  report.task_done_on_server_while_minimized = (await api.tasks(tok, "luna")).items.some((t) => t.id === t1.id && t.state === "COMPLETED");
  report.rehydrated_after_restore = await until(() => seq() > s0, 15000);
  report.connected_after_restore = await until(connected);
  // 2) fechar a janela: vai para a bandeja (não encerra); o servidor segue; reabrir reidrata
  const s1 = seq();
  await w.close();
  await wait(1500);
  report.hidden_after_close = !(await w.isVisible());
  const t2 = await api.createTask(tok, "luna", "research.mock", { topic: "autoteste janela fechada" });
  await wait(4000);
  await w.show(); await w.setFocus();
  report.task_done_on_server_while_closed = (await api.tasks(tok, "luna")).items.some((t) => t.id === t2.id && t.state === "COMPLETED");
  report.rehydrated_after_reopen = await until(() => seq() > s1, 15000);
  report.connected_after_reopen = await until(connected);
  // 3) abrir com o sistema (opt-in): liga, confere e volta ao estado original (padrão: desligado)
  const { autostartGet, autostartSet } = await import("./native");
  const before = await autostartGet();
  report.autostart_default_off = before === false;
  report.autostart_enable_ok = (await autostartSet(true)) === true;
  report.autostart_restored = (await autostartSet(!!before)) === !!before;
  sessionStorage.removeItem(`${KEY}.mode`);
  await post(base, report);
}

/** Modo "update": prova a atualização automática no app real. A versão antiga acha a nova no servidor, baixa,
 *  confere a assinatura (chave embutida), instala e reinicia; a nova relata a própria versão e "sem atualização". */
async function runUpdateSelfTest(base: string): Promise<void> {
  const { appVersion, updateCheck, updateInstall } = await import("./native");
  const { apiUrl } = await import("@/lib/api");
  const version = await appVersion();
  await post(base, { phase: "update-launch", version });
  try {
    const found = await updateCheck(apiUrl());
    if (!found) { await post(base, { phase: "update-none", version }); return; }
    await post(base, { phase: "update-found", version, found: found.version, notes: found.notes });
    await updateInstall(apiUrl()); // reinicia o app ao terminar
  } catch (e) {
    await post(base, { phase: "update-error", version, error: e instanceof Error ? e.message : String(e) });
  }
}

export async function runSelfTest(): Promise<void> {
  const base = target();
  if (!base) return;
  sessionStorage.setItem(KEY, base);
  const mode = new URLSearchParams(window.location.search).get("mode") ?? sessionStorage.getItem(`${KEY}.mode`);
  if (mode === "device") { sessionStorage.setItem(`${KEY}.mode`, "device"); return runDeviceSelfTest(base); }
  if (mode === "window") { sessionStorage.setItem(`${KEY}.mode`, "window"); return runWindowSelfTest(base); }
  if (mode === "update") return runUpdateSelfTest(base); // sem estado em sessionStorage: o app reinicia
  if (!sessionStorage.getItem("aic.token")) {
    const tok = await (await fetch(`${base}/token`)).text();
    sessionStorage.setItem("aic.token", tok.trim());
    localStorage.setItem("aic.desk.page", "command");
    localStorage.setItem("aic.debug", "1");
    window.location.reload();
    return;
  }
  const phase = sessionStorage.getItem(`${KEY}.phase`) ?? "command";
  const report: Record<string, unknown> = { phase, user_agent: navigator.userAgent, viewport: [innerWidth, innerHeight], dpr: devicePixelRatio };
  report.nav = await until(() => !!document.querySelector('nav[aria-label="Principal"]'));
  report.connected = await until(() => /Conectado/.test(text(".side-foot [role=status]") ?? ""));
  report.connection_label = text(".side-foot [role=status]");
  report.mode = text(".side-foot .dk-chip");
  if (phase === "command") {
    report.loaded = await until(() => !document.body.textContent?.includes("Carregando…"));
    report.kpis = [...document.querySelectorAll(".dk-kpi .val")].map((e) => e.textContent);
    sessionStorage.setItem(`${KEY}.phase`, "office-high");
    localStorage.setItem("aic.desk.page", "office");
    localStorage.setItem("aic.quality", "high");
    await post(base, report);
    window.location.reload();
    return;
  }
  report.canvas = await until(() => !!document.querySelector("canvas"));
  report.scene_ready = await until(() => !!document.querySelector("[data-ready='1']"));
  report.quality = localStorage.getItem("aic.quality");
  report.rooms = [...document.querySelectorAll('nav[aria-label="Salas do escritório"] button')].map((e) => e.textContent);
  report.agents = document.querySelectorAll('nav[aria-label="Agentes no escritório"] button[aria-pressed]').length;
  report.nameplates = [...document.querySelectorAll(".nameplate")].map((e) => e.textContent);
  await wait(2500);
  const gl = (window as unknown as { __aicGl?: { getContext(): WebGL2RenderingContext; info: { render: { calls: number; triangles: number } } } }).__aicGl;
  if (gl) {
    const ctx = gl.getContext();
    const dbg = ctx.getExtension("WEBGL_debug_renderer_info");
    report.renderer = dbg ? ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : ctx.getParameter(ctx.RENDERER);
    report.webgl2 = typeof WebGL2RenderingContext !== "undefined" && ctx instanceof WebGL2RenderingContext;
    report.draw_calls = gl.info.render.calls;
    report.triangles = gl.info.render.triangles;
  }
  const t0 = performance.now(); let frames = 0;
  let worst = 0; let last = t0;
  // janela encoberta: o WebKit pausa o requestAnimationFrame — reporta em vez de travar
  report.raf_stalled = !(await Promise.race([
    new Promise<boolean>((res) => { const f = () => { const now = performance.now(); worst = Math.max(worst, now - last); last = now; frames++; if (now - t0 < 10000) requestAnimationFrame(f); else res(true); }; requestAnimationFrame(f); }),
    wait(20000).then(() => false),
  ]));
  report.seconds = 10;
  report.fps = Math.round((frames / ((performance.now() - t0) / 1000)) * 10) / 10;
  report.worst_frame_ms = Math.round(worst);
  report.reduced_motion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (phase === "office-high") {
    sessionStorage.setItem(`${KEY}.phase`, "office-low");
    localStorage.setItem("aic.quality", "low");
    await post(base, report);
    window.location.reload();
    return;
  }
  localStorage.setItem("aic.quality", "auto");
  sessionStorage.removeItem(`${KEY}.phase`);
  await post(base, report);
}

async function post(base: string, body: Record<string, unknown>) {
  await fetch(`${base}/report`, { method: "POST", body: JSON.stringify(body) }).catch(() => undefined);
}
