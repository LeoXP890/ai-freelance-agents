// Cenários E2E mandatórios 3, 5 e 6 do SDD v3 §12 contra o stack DRY_RUN real (API, worker e WebSocket reais —
// sem mocks de presença): missão aparecendo na lista e nos agentes, aprovação ponta a ponta com um proprietário
// de teste (MFA real, criado e desativado pelo próprio teste) e queda/volta de rede com bloqueio de mutações.
import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

const AFTER = new URL("../../../docs/desktop-migration/screenshots/after/", import.meta.url);
const ROOT = new URL("../../../", import.meta.url).pathname;
const ML_ITEM = JSON.parse(readFileSync(new URL("./fixtures/ml_item.json", import.meta.url), "utf8"));
const COMPOSE = ["compose", "-f", "infra/compose/docker-compose.yml", "--env-file", "infra/env/dry_run.env"];
const shot = (page: Page, name: string) => page.screenshot({ path: new URL(name, AFTER).pathname });

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

async function login(req: APIRequestContext, email: string, password = "simulado-123") {
  const r = await req.post("/api/v1/auth/login", { data: { email, password } });
  return (await r.json()).access_token as string;
}

/** Abre o app já autenticado, com a sincronização REAL (snapshot + WS do backend). */
async function openReal(page: Page, token: string, pageId: string) {
  await page.addInitScript(({ tok, p }) => {
    sessionStorage.setItem("aic.token", tok);
    localStorage.setItem("aic.desk.page", p);
  }, { tok: token, p: pageId });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();
  await expect(page.locator(".side-foot").getByRole("status")).toContainText(/Conectado/, { timeout: 15_000 });
}

test("3 · missão em DRY_RUN aparece na lista e nos agentes ao mesmo tempo, com linha do tempo real", async ({ page }) => {
  test.setTimeout(90_000);
  await openReal(page, await login(page.request, "operator@demo.local"), "missions");
  await expect(page.getByRole("heading", { level: 1, name: "Missões & tarefas" })).toBeVisible();
  const goal = `avaliar nicho de organizadores (E2E ${Date.now() % 100000})`;
  await page.getByRole("button", { name: "Nova missão" }).click();
  await page.getByPlaceholder(/avaliar o nicho/).fill(goal);
  await page.getByRole("button", { name: "Iniciar missão" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Missão criada no servidor" })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: goal });
  await expect(row).toBeVisible({ timeout: 5_000 });
  // o worker real executa: a lista avança sozinha pelos eventos do WebSocket (sem recarregar a página)
  await expect(row.getByText("Concluída")).toBeVisible({ timeout: 45_000 });
  await expect(row).toContainText("3/3");
  const pane = page.getByRole("complementary", { name: "Detalhe da missão" });
  await expect(pane.getByRole("heading", { name: goal })).toBeVisible();
  await expect(pane.getByRole("region", { name: "Etapas" }).getByRole("listitem")).toHaveCount(3);
  await expect(pane.getByRole("region", { name: "Linha do tempo" })).toContainText("Etapas distribuídas aos agentes");
  await expect(pane.getByRole("region", { name: "Linha do tempo" })).toContainText("Resumo final gerado");
  await expect(pane.getByRole("region", { name: "Resultado" })).toBeVisible();
  await shot(page, "07-missoes-detalhe-1440x900.png");
  // mesmo PresenceStore: a Luna (etapa de pesquisa) aparece atualizada "agora" no diretório
  await page.keyboard.press("ControlOrMeta+3");
  await page.getByPlaceholder("Buscar por nome, função ou área").fill("luna");
  await expect(page.locator(".agent-card").first()).toContainText("agora");
  // reexecução cria uma NOVA missão e preserva a original
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Missões & tarefas" }).click();
  await page.getByRole("button", { name: `Abrir missão: ${goal}` }).first().click();
  await page.getByRole("button", { name: "Reexecutar simulação" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Nova missão criada com o mesmo objetivo" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: goal })).toHaveCount(2);
  await expect(pane.getByText(/Reexecução da missão/)).toBeVisible();
});

test.describe("5 · aprovação ponta a ponta", () => {
  const email = `e2e-owner-${Date.now()}@test.local`;
  const password = "e2e-senha-bem-longa-123";
  test.afterAll(() => {
    execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-m", "aicommerce.manage", "deactivate-user", "--email", email], { cwd: ROOT });
  });

  test("proposta simulada → inbox priorizada → dossiê → aprovar → servidor confirma e audita", async ({ page }) => {
    test.setTimeout(90_000);
    execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-m", "aicommerce.manage", "create-user",
      "--email", email, "--name", "Proprietário E2E", "--role", "owner", "--password-stdin"], { cwd: ROOT, input: `${password}\n` });
    const first = await (await page.request.post("/api/v1/auth/login", { data: { email, password } })).json();
    expect(first.mfa_enrollment_required).toBe(true);
    const enroll = await (await page.request.post("/api/v1/auth/mfa/enroll/start", { data: { enrollment_token: first.enrollment_token } })).json();
    const conf = await page.request.post("/api/v1/auth/mfa/enroll/confirm", { data: { enrollment_token: first.enrollment_token, code: totp(enroll.secret) } });
    expect(conf.ok()).toBe(true);
    const ownerToken = (await conf.json()).access_token as string;

    const op = await login(page.request, "operator@demo.local");
    const sku = `E2E-${Date.now()}`;
    const prop = await (await page.request.post("/api/v1/proposals", {
      headers: { Authorization: `Bearer ${op}` },
      data: { action_type: "PUBLISH_LISTING", agent_id: "nova", subject_type: "listing", subject_id: sku, subject_version: 1,
        payload: { ...ML_ITEM, seller_custom_field: sku }, risk: "MEDIUM", evidence_refs: [`dossier:${sku}`], idempotency_key: sku,
        go_live: { channel: "MERCADO_LIVRE", supplier_origin: "NATIONAL", fulfillment_mode: "DROPSHIP_SUPPLIER", uf: "SP" } },
    })).json();
    expect(prop.status).toBe("PENDING_APPROVAL");

    // workspace da Nova: "Precisa de você" lista a proposta; clicar abre a Central já com ela selecionada (sem decidir)
    await openReal(page, ownerToken, "agents");
    await page.locator(".agent-card", { hasText: "Nova" }).first().click();
    const needs = page.getByRole("complementary", { name: "Agente Nova" }).locator("[aria-label='Precisa de você']");
    await expect(needs).toContainText(ML_ITEM.title);
    await needs.getByRole("button", { name: new RegExp(ML_ITEM.title.replace(/[()]/g, "\\$&")) }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "Aprovações" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Dossiê da proposta" })).toBeVisible();
    const inbox = page.getByRole("region", { name: "Caixa de propostas" });
    await expect(inbox.getByRole("button").first()).toBeVisible();
    await shot(page, "09-aprovacoes-inbox-1440x900.png");
    await page.getByLabel("Buscar propostas").fill(sku); // a inbox pode ter outras pendentes: isola a do teste
    await expect(inbox.getByRole("button")).toHaveCount(1);
    await expect(inbox.getByRole("button")).toContainText("Simulado");
    // a linha identifica a proposta: objeto, canal, agente e risco (não só "Publicar anúncio")
    await expect(inbox.getByRole("button")).toContainText(ML_ITEM.title);
    await expect(inbox.getByRole("button")).toContainText("Mercado Livre");
    await expect(inbox.getByRole("button")).toContainText("Nova");
    await expect(inbox.getByRole("button")).toContainText(/Risco (baixo|médio|alto)/);
    await inbox.getByRole("button").click();
    const dossier = page.getByRole("complementary", { name: "Dossiê da proposta" });
    await expect(dossier.getByText(`Proposta #${prop.id.slice(0, 8)}`)).toBeVisible();
    await expect(dossier.getByText("Por que precisa de você")).toBeVisible();
    await expect(dossier.getByText("O que será executado")).toBeVisible();
    await expect(dossier.getByText("Evidências (1)")).toBeVisible();
    await shot(page, "10-aprovacao-dossie-1440x900.png");
    const badge = page.getByRole("navigation", { name: "Principal" }).locator(".nav-count");
    const before = Number(await badge.innerText());
    await dossier.getByRole("button", { name: "Aprovar" }).click();
    const dlg = page.getByRole("alertdialog", { name: "Aprovar esta proposta?" });
    await expect(dlg).toContainText("A execução só começa depois que o servidor confirmar");
    await dlg.getByRole("button", { name: "Confirmar aprovação" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Decisão registrada no servidor" })).toBeVisible();
    await expect(dossier.getByRole("button", { name: "Aprovar" })).toHaveCount(0);
    if (before > 1) await expect(badge).toHaveText(String(before - 1)); // contador da barra lateral atualizado na hora
    else await expect(badge).toHaveCount(0);

    const after = await (await page.request.get(`/api/v1/approvals/${prop.id}`, { headers: { Authorization: `Bearer ${ownerToken}` } })).json();
    expect(after.status).not.toBe("PENDING_APPROVAL");
    expect(after.decisions.map((d: { decision: string }) => d.decision)).toEqual(["APPROVE"]);
    const audit = await (await page.request.get(`/api/v1/audit/events?subject_id=${prop.id}`, { headers: { Authorization: `Bearer ${ownerToken}` } })).json();
    expect(audit.items.some((e: { action: string }) => e.action.startsWith("approval."))).toBe(true);
    await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Histórico & auditoria" }).click();
    // filtra pelo assunto do teste: a captura nunca mostra contas reais que existam no banco local
    await page.getByPlaceholder("ID do assunto (pedido, proposta, missão…)").fill(prop.id);
    await expect(page.getByRole("region", { name: "Eventos de auditoria" }).getByRole("row").nth(1)).toBeVisible();
    await page.getByRole("button", { name: "Verificar integridade" }).click();
    await expect(page.getByText("Cadeia íntegra")).toBeVisible();
    await shot(page, "12-auditoria-1440x900.png");
  });
});

test("6 · queda de rede: aviso com dados em cache, mutações bloqueadas e reconciliação ao voltar", async ({ page, context }) => {
  await openReal(page, await login(page.request, "operator@demo.local"), "missions");
  await expect(page.getByRole("button", { name: "Nova missão" })).toBeEnabled();
  await context.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  const strip = page.getByRole("alert").filter({ hasText: "Sem conexão com o servidor" });
  await expect(strip).toBeVisible();
  await expect(strip).toContainText("os agentes continuam trabalhando no servidor");
  await expect(page.locator(".side-foot").getByRole("status")).toContainText("Sem conexão");
  await expect(page.getByRole("button", { name: "Nova missão" })).toBeDisabled();
  await expect(page.getByRole("table")).toBeVisible(); // dados já carregados continuam visíveis
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Canais de venda" }).click();
  await expect(page.getByRole("button", { name: "Preparar anúncio" })).toBeDisabled();
  await shot(page, "13-sem-conexao-1440x900.png");
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByRole("status").filter({ hasText: "Conexão restabelecida" })).toBeVisible({ timeout: 20_000 });
  await expect(strip).toBeHidden();
  await expect(page.getByRole("button", { name: "Preparar anúncio" })).toBeEnabled();
});

test("base de conhecimento: lista com ACL, busca dos agentes e telas a 1366×768 sem rolagem horizontal", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openReal(page, await login(page.request, "operator@demo.local"), "knowledge");
  await expect(page.getByRole("heading", { level: 1, name: "Base de conhecimento" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Documentos" }).getByRole("row").nth(1)).toBeVisible();
  await page.getByLabel("Buscar no conteúdo dos documentos").fill("prazo de devolução por arrependimento");
  await page.getByRole("button", { name: "Buscar" }).click();
  await expect(page.getByRole("region", { name: "Resultados da busca" })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 900 });
  await shot(page, "11-base-de-conhecimento-1440x900.png");
  await page.setViewportSize({ width: 1366, height: 768 });
  for (const label of ["Missões & tarefas", "Aprovações", "Base de conhecimento"]) {
    await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: label }).click();
    await page.waitForTimeout(400);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over, label).toBeLessThanOrEqual(0);
  }
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Missões & tarefas" }).click();
  await page.locator(".dk-table tbody tr").first().click();
  await expect(page.getByRole("complementary", { name: "Detalhe da missão" }).getByRole("region", { name: "Etapas" })).toBeVisible();
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
  await shot(page, "08-missoes-1366x768.png");
});

test("telas de negócio nativas (Fase 5): abrem com dados reais, detalhe lateral e sem rolagem horizontal a 1366×768", async ({ page }) => {
  test.setTimeout(120_000);
  const CAMPAIGNS = JSON.parse(readFileSync(new URL("./fixtures/campaigns.json", import.meta.url), "utf8"));
  await page.route("**/v1/campaigns", (r) => r.fulfill({ json: CAMPAIGNS }));
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await openReal(page, await login(page.request, "operator@demo.local"), "finance");
  const nav = page.getByRole("navigation", { name: "Principal" });
  const pages: [string, string, string | null][] = [
    ["Financeiro", "Margem por canal", null],
    ["Pedidos & logística", "Lista de pedidos", "Detalhe do pedido"],
    ["Canais de venda", "Anúncios", "Detalhe do anúncio"],
    ["Fornecedores", "Comparativo de fornecedores", "Dossiê do fornecedor"],
    ["Produtos & pesquisa", "Catálogo mestre", "Detalhe do produto"],
    ["Campanhas", "Campanhas", "Detalhe da campanha"],
    ["Relatórios", "Receita líquida por canal", null],
    ["Homologações", "Gates de go-live", "Detalhe do gate"],
  ];
  const shots: Record<string, string> = { "Financeiro": "14-financeiro", "Pedidos & logística": "15-pedidos", "Canais de venda": "16-canais",
    "Fornecedores": "17-fornecedores", "Produtos & pesquisa": "18-produtos", "Campanhas": "19-campanhas", "Relatórios": "20-relatorios", "Homologações": "21-homologacoes" };
  for (const [label, region, pane] of pages) {
    await nav.getByRole("button", { name: label }).click();
    await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
    await expect(page.getByText("Layout em transição")).toHaveCount(0);
    await expect(page.getByRole("region", { name: region }).first()).toBeVisible();
    if (pane) {
      // stack limpa (CI) pode não ter pedidos/anúncios: com linhas abre o detalhe; sem linhas, o estado vazio explica
      const rows = page.locator(".dk-table tbody tr:not(.group-row):not(.static)");
      await expect(rows.first().or(page.locator(".empty").first())).toBeVisible({ timeout: 10_000 });
      if (await rows.count()) {
        await rows.first().click();
        await expect(page.getByRole("complementary", { name: pane })).toBeVisible();
      } else {
        await expect(page.locator(".empty").first()).toBeVisible();
      }
    }
    await expect(page.getByText("Carregando…")).toHaveCount(0, { timeout: 10_000 });
    await page.screenshot({ path: new URL(`${shots[label]}-1440x900.png`, AFTER).pathname });
  }
  for (const [w, h] of [[1366, 768], [1920, 1080]] as const) { // cenário E2E 9: as duas resoluções do SDD
    await page.setViewportSize({ width: w, height: h });
    for (const [label] of pages) {
      await nav.getByRole("button", { name: label }).click();
      await page.waitForTimeout(250);
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over, `${label} @${w}`).toBeLessThanOrEqual(0);
    }
  }
  expect(errors).toEqual([]);
});

test("UX-03 conversas: pergunta real ao agente, resposta com fonte/rota, histórico listado e continuação com outro agente", async ({ page }) => {
  await openReal(page, await login(page.request, "operator@demo.local"), "chat");
  await expect(page.getByRole("region", { name: "Histórico da conversa" })).toBeVisible();
  await page.getByLabel("Agente", { exact: true }).selectOption("eva");
  const q = `Qual o prazo de devolução por arrependimento? (E2E ${Date.now() % 10000})`;
  await page.getByLabel("Mensagem").fill(q);
  await page.keyboard.press("Enter");
  const log = page.locator(".chat-log");
  await expect(log.locator(".msg-me").last()).toContainText(q);
  await expect(log.locator(".msg-agent").last()).toContainText("Eva", { timeout: 20_000 });
  await expect(log.locator(".msg-agent").last()).toContainText(/base de conhecimento|dados do sistema|regras|resposta geral/);
  await expect(page.getByRole("complementary", { name: "Conversas" }).getByRole("button").filter({ hasText: q })).toHaveCount(1);
  await page.getByLabel("Agente", { exact: true }).selectOption("finn");
  await page.getByLabel("Mensagem").fill("E quanto temos de orçamento disponível?");
  await page.getByRole("button", { name: "Enviar" }).click();
  await expect(log.getByText("Conversa continuada com Finn")).toBeVisible();
  await expect(log.locator(".msg-agent").last()).toContainText("Finn", { timeout: 20_000 });
  await expect(page.getByRole("complementary", { name: "Contexto do agente" })).toContainText("Finn");
  await page.screenshot({ path: new URL("22-conversas-1440x900.png", AFTER).pathname });
});

test("UXD-005: tabela ordena e esconde colunas (lembrado), Skeleton anuncia carregamento, tema claro aplicado", async ({ page }) => {
  // tabela da Base de conhecimento: o seed sempre tem documentos (independe de pedidos no banco)
  await openReal(page, await login(page.request, "operator@demo.local"), "knowledge");
  const table = page.getByRole("table", { name: "Documentos da base" });
  await expect(table).toBeVisible();
  const header = table.getByRole("columnheader", { name: "Documento" });
  await expect(header).toHaveAttribute("aria-sort", "ascending"); // ordenação inicial
  const titles = async () => (await table.locator("tbody tr td:first-child strong").allInnerTexts());
  const asc = await titles();
  expect(asc).toEqual([...asc].sort((a, b) => a.localeCompare(b, "pt-BR", { numeric: true })));
  await header.getByRole("button").click();
  await expect(header).toHaveAttribute("aria-sort", "descending");
  expect(await titles()).toEqual([...asc].reverse());
  await page.getByRole("button", { name: "Colunas" }).click();
  await page.getByRole("group", { name: "Colunas de Documentos da base" }).getByLabel("Fonte").uncheck();
  await expect(table.getByRole("columnheader", { name: "Fonte" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("table", { name: "Documentos da base" }).getByRole("columnheader", { name: "Fonte" })).toHaveCount(0); // lembrado
  await page.evaluate(() => localStorage.removeItem("aic.cols.kb"));
  // tema claro: preferência em Configurações muda o tema de toda a interface
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Configurações" }).click();
  await page.getByRole("combobox", { name: "Tema" }).selectOption("light");
  await expect(page.locator(".desk")).toHaveAttribute("data-theme", "light");
  const bg = await page.locator(".desk").evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(bg).toBe("rgb(244, 241, 236)");
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Centro de comando" }).click();
  await expect(page.getByText("Carregando…")).toHaveCount(0, { timeout: 10_000 });
  await page.screenshot({ path: new URL("23-tema-claro-centro-1440x900.png", AFTER).pathname });
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Configurações" }).click();
  await page.getByRole("combobox", { name: "Tema" }).selectOption("system");
});

test("COM-001 · foto do computador: enviada, regravada pelo servidor e exibida no editor do produto", async ({ page }) => {
  await openReal(page, await login(page.request, "operator@demo.local"), "products");
  const catalog = page.getByRole("region", { name: "Catálogo mestre" });
  await catalog.locator("tbody tr").first().click();
  const pane = page.getByRole("complementary", { name: "Detalhe do produto" });
  await expect(pane.getByRole("heading", { name: /Imagens/ })).toBeVisible();
  const before = await pane.locator(".media-edit").count();
  await pane.getByLabel("Enviar fotos do computador").setInputFiles(new URL("./fixtures/foto-produto.jpg", import.meta.url).pathname);
  await expect(pane.getByRole("status").filter({ hasText: "1 foto(s) enviada(s)" })).toBeVisible();
  await expect(pane.locator(".media-edit")).toHaveCount(before + 1);
  const thumb = pane.locator(".media-edit").last().locator("img.thumb");
  await expect(thumb).toHaveAttribute("src", /\/api\/v1\/media\/[0-9a-f]{64}\.jpg$/);
  await expect.poll(() => thumb.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBe(640); // servida pela API
  await expect(pane.getByRole("button", { name: /Salvar/ })).toBeVisible(); // só fica no produto ao salvar (versionado)
});

test("UXD-011 · dossiê mostra o que muda em relação à proposta anterior do mesmo anúncio", async ({ page }) => {
  const op = await login(page.request, "operator@demo.local");
  const sku = `E2E-CMP-${Date.now()}`;
  const send = async (price: number, key: string) => (await (await page.request.post("/api/v1/proposals", {
    headers: { Authorization: `Bearer ${op}` },
    data: { action_type: "PUBLISH_LISTING", agent_id: "nova", subject_type: "listing", subject_id: sku, subject_version: 1,
      payload: { ...ML_ITEM, seller_custom_field: sku, price }, risk: "MEDIUM", evidence_refs: [`dossier:${sku}`], idempotency_key: key,
      go_live: { channel: "MERCADO_LIVRE", supplier_origin: "NATIONAL", fulfillment_mode: "DROPSHIP_SUPPLIER", uf: "SP" } },
  })).json()) as { id: string };
  await send(89.9, `${sku}-a`);
  const second = await send(74.9, `${sku}-b`);
  await openReal(page, op, "approvals");
  await page.getByLabel("Buscar propostas").fill(sku); // as duas propostas do mesmo anúncio
  const inbox = page.getByRole("region", { name: "Caixa de propostas" });
  await expect(inbox.getByRole("button")).toHaveCount(2);
  const dossier = page.getByRole("complementary", { name: "Dossiê da proposta" });
  for (const i of [0, 1]) { // abre a mais recente (a ordem da inbox é por prioridade, não por data)
    await inbox.getByRole("button").nth(i).click();
    await expect(dossier.getByText(/Proposta #/)).toBeVisible();
    if (await dossier.getByText(`Proposta #${second.id.slice(0, 8)}`).count()) break;
  }
  await expect(dossier.getByText(`Proposta #${second.id.slice(0, 8)}`)).toBeVisible();
  const cmp = dossier.getByRole("region", { name: "Comparação com a versão anterior" });
  await expect(cmp).toContainText("à proposta anterior (não executada)");
  const row = cmp.getByRole("row").filter({ hasText: "Preço" });
  await expect(row).toContainText("89.9");
  await expect(row).toContainText("74.9");
});

test("PF/CPF (ADR 0006): perfil Pessoa Física sem CPF guardado, canais só em simulação com motivo e requisitos do perfil", async ({ page }) => {
  const op = await login(page.request, "operator@demo.local");
  // abrir Canais direto (1ª tela após o login) também carrega homologações e elegibilidade — antes não carregava
  await openReal(page, op, "channels");
  await expect(page.locator(".channel-card", { hasText: "Shopee" })).toContainText("Só simulação");
  await expect(page.locator(".elig-card")).toBeVisible();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Configurações" }).click();
  const card = page.getByRole("region", { name: "Perfil do vendedor" });
  await expect(card).toContainText("Pessoa Física (CPF)");
  await expect(card).toContainText("não é armazenado");
  await expect(card).toContainText("CPF não é autorização");
  await expect(card).not.toContainText("Abra seu CNPJ");
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Canais de venda" }).click();
  for (const ch of ["Mercado Livre", "Shopee", "TikTok Shop", "Loja própria (Woo)"]) {
    await expect(page.locator(".channel-card", { hasText: ch })).toContainText("Só simulação");
  }
  await page.getByText("Elegibilidade por canal — Pessoa Física").click();
  await expect(page.locator(".elig-card")).toContainText("Não verificada");
  await page.getByRole("navigation", { name: "Principal" }).getByRole("button", { name: "Homologações" }).click();
  await expect(page.getByRole("note")).toContainText("Pessoa Física");
  await page.getByRole("button", { name: /Abrir gate LEG-01/ }).click();
  const pane = page.getByRole("complementary", { name: "Detalhe do gate" });
  await expect(pane).toContainText("Elegibilidade da Pessoa Física para o modelo de venda");
  await expect(pane.getByRole("region", { name: "Requisitos do gate" })).toContainText("Parecer de profissional habilitado");
  await expect(pane).not.toContainText("CNPJ, CNAE");
});
