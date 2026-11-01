// Apresentação de missões e aprovações no app desktop (SDD v3 UX-04/UX-05). Funções puras: só traduzem o que o
// backend devolveu para linguagem de operação — nunca inventam estado, prioridade "de mentira" ou progresso.
import type { MissionBlocker, Proposal } from "./api";
import { formatMinor } from "./format";

export type Tone = "ok" | "info" | "warn" | "crit" | "muted";

export const TASK_STATE: Record<string, { label: string; tone: Tone }> = {
  CREATED: { label: "Criada", tone: "muted" },
  QUEUED: { label: "Na fila", tone: "muted" },
  RUNNING: { label: "Em andamento", tone: "info" },
  WAITING_EXTERNAL: { label: "Aguardando ferramenta", tone: "info" },
  WAITING_APPROVAL: { label: "Aguardando aprovação", tone: "warn" },
  RESUMED: { label: "Retomada", tone: "info" },
  COMPLETED: { label: "Concluída", tone: "ok" },
  FAILED: { label: "Falhou", tone: "crit" },
  CANCELLED: { label: "Cancelada", tone: "muted" },
};

export const MISSION_BLOCKER: Record<MissionBlocker, { label: string; hint: string; tone: Tone }> = {
  WAITING_APPROVAL: { label: "Aguardando aprovação", tone: "warn",
    hint: "Uma etapa gerou uma proposta que só segue depois da sua decisão na Central de Aprovações." },
  WAITING_TOOL: { label: "Aguardando ferramenta", tone: "info",
    hint: "Os agentes estão executando as etapas; o Atlas retoma e resume quando todas terminarem." },
  BLOCKED_POLICY: { label: "Bloqueado por política", tone: "crit",
    hint: "Um limite ou regra de segurança impediu a execução. Ajuste a política ou o objetivo antes de reexecutar." },
  RECOVERABLE: { label: "Falha recuperável", tone: "warn",
    hint: "Falha temporária (modelo, rede ou worker). Reexecutar a simulação cria uma nova missão com o mesmo objetivo." },
  FAILED: { label: "Falhou", tone: "crit", hint: "A missão terminou com erro. Veja a etapa com falha abaixo." },
};

export function formatDuration(s: number): string {
  if (s < 1) return "< 1 s"; // missão simulada termina em milissegundos: "0 s" parecia falha
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60 ? `${s % 60} s` : ""}`.trim();
  const h = Math.floor(s / 3600);
  if (h < 48) return `${h} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.floor(h / 24)} d`;
}

export const CHECKPOINT_LABEL: Record<string, string> = {
  context_built: "Contexto e fontes reunidos",
  persisted: "Resultado validado e salvo",
  children_spawned: "Etapas distribuídas aos agentes",
  children_done: "Todas as etapas terminaram",
  summarized: "Resumo final gerado",
};

/** Uma linha da linha do tempo, em português, a partir de um evento real de estado de tarefa. */
export function describeEvent(e: { type: string; task_state: string | null; from: string | null; tool: string | null;
  detail: Record<string, unknown> }): string {
  if (e.tool) return `Usou a ferramenta ${e.tool}`;
  if (e.type === "agent.task.created") return "Tarefa criada";
  const to = e.task_state ? (TASK_STATE[e.task_state]?.label ?? e.task_state) : null;
  if (!to) return e.type;
  const kids = typeof e.detail.children === "number" ? ` (${e.detail.children} etapas)` : "";
  const err = typeof e.detail.error_kind === "string" ? ` — ${e.detail.error_kind}` : "";
  const why = e.detail.reason === "mission_cancelled" ? " — missão cancelada" : "";
  return `${to}${kids}${err}${why}`;
}

// ───────────── Aprovações ─────────────
export const URGENT_HOURS = 24;
export const HIGH_RISK = 60;

export interface Priority { urgent: boolean; risk: boolean; value: boolean; rank: number; hoursLeft: number }

/** Prioridade da inbox: urgente (expira em < 24 h), risco (score ≥ 60) e valor (há dinheiro envolvido). */
export function approvalPriority(p: Pick<Proposal, "status" | "expires_at" | "risk_score" | "monetary_scope_minor">,
  now = Date.now()): Priority {
  const hoursLeft = (new Date(p.expires_at).getTime() - now) / 3_600_000;
  const pending = p.status === "PENDING_APPROVAL";
  const urgent = pending && hoursLeft < URGENT_HOURS;
  const risk = p.risk_score >= HIGH_RISK;
  const value = p.monetary_scope_minor > 0;
  const rank = (pending ? 1000 : 0) + (urgent ? 400 : 0) + (risk ? 200 : 0) + (value ? 100 : 0)
    + Math.max(0, 99 - Math.min(99, Math.floor(Math.max(0, hoursLeft))));
  return { urgent, risk, value, rank, hoursLeft };
}

export function sortInbox<T extends Pick<Proposal, "status" | "expires_at" | "risk_score" | "monetary_scope_minor" | "created_at">>(
  items: T[], now = Date.now()): T[] {
  return [...items].sort((a, b) => approvalPriority(b, now).rank - approvalPriority(a, now).rank
    || String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
}

export function expiresIn(iso: string, now = Date.now()): string {
  const h = (new Date(iso).getTime() - now) / 3_600_000;
  if (h <= 0) return "expirada";
  if (h < 1) return `expira em ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `expira em ${Math.floor(h)} h`;
  return `expira em ${Math.floor(h / 24)} d`;
}

const PAYLOAD_LABEL: Record<string, string> = {
  reason: "Motivo", channel: "Canal", order_id: "Pedido", order_external_id: "Pedido no canal", refund_minor: "Reembolso",
  withdrawal_right: "Direito de arrependimento", listing_id: "Anúncio", price_minor: "Preço", title: "Título",
  budget_minor: "Orçamento", daily_budget_minor: "Orçamento diário", cap_minor: "Teto", campaign_id: "Campanha",
  supplier_id: "Fornecedor", offer_id: "Oferta", variant_id: "Variante", sku: "SKU", qty: "Quantidade",
  amount_minor: "Valor", cost_minor: "Custo", description: "Descrição", name: "Nome", objective: "Objetivo",
  external_id: "ID no canal", product_id: "Produto", topic: "Tema", duration_days: "Duração (dias)",
  // contrato do Mercado Livre (anúncio)
  price: "Preço", available_quantity: "Estoque", buying_mode: "Modo de compra", listing_type_id: "Tipo de anúncio",
  condition: "Condição", category_id: "Categoria", currency_id: "Moeda", pictures: "Imagens", attributes: "Atributos",
  seller_custom_field: "SKU do vendedor",
};
const VALUE_LABEL: Record<string, string> = { new: "Novo", used: "Usado", buy_it_now: "Compra imediata", gold_special: "Clássico",
  gold_pro: "Premium" };

function arrayText(v: unknown[]): string {
  if (v.length === 0) return "nenhum";
  if (v.every((x) => x && typeof x === "object" && "id" in x && "value_name" in x)) {
    return (v as { id: string; value_name: string }[]).map((x) => `${x.id}: ${x.value_name}`).join(" · ");
  }
  if (v.every((x) => typeof x !== "object")) return v.join(", ");
  return JSON.stringify(v);
}

/** Conteúdo proposto em linhas legíveis (valores *_minor em BRL, booleanos em Sim/Não). JSON bruto fica à parte. */
export interface EconomicsLine { key: string; label: string; amount_minor: number; source: string }
export interface Economics { lines: EconomicsLine[]; profit_minor: number; margin_pct: number | null; currency: string }

/** Conta por venda anexada à proposta (servidor confere nas fontes antes de pedir aprovação). */
export function economicsOf(payload: Record<string, unknown>): Economics | null {
  const e = payload.economics as Economics | undefined;
  return e && Array.isArray(e.lines) && typeof e.profit_minor === "number" ? e : null;
}

export interface MarketOffer { store: string; product: string; price: number; free_shipping: boolean; url: string }
export interface MarketCheck { count: number; min: number | null; median: number | null; max: number | null; offers: MarketOffer[]; note: string }

/** Concorrência pesquisada no Google antes da proposta (obrigatória para publicar). */
export function marketCheckOf(payload: Record<string, unknown>): MarketCheck | null {
  const m = payload.market_check as MarketCheck | undefined;
  return m && Array.isArray(m.offers) ? m : null;
}

const HIDDEN_PAYLOAD_KEYS = new Set(["economics", "market_check"]);

export function payloadRows(payload: Record<string, unknown>, currency = "BRL"): { key: string; label: string; value: string }[] {
  return Object.entries(payload).filter(([k]) => !HIDDEN_PAYLOAD_KEYS.has(k)).map(([k, v]) => {
    const label = PAYLOAD_LABEL[k] ?? k.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
    let value: string;
    if (typeof v === "number" && k.endsWith("_minor")) value = formatMinor(v, currency);
    else if (typeof v === "number" && k === "price") value = formatMinor(Math.round(v * 100), String(payload.currency_id ?? currency));
    else if (typeof v === "boolean") value = v ? "Sim" : "Não";
    else if (Array.isArray(v)) value = arrayText(v);
    else if (typeof v === "string" && VALUE_LABEL[v]) value = VALUE_LABEL[v]!;
    else if (v === null || v === undefined) value = "—";
    else if (typeof v === "object") value = JSON.stringify(v);
    else value = String(v);
    return { key: k, label, value };
  });
}

/** Nível de risco legível a partir do escore da política (0–100). */
export function riskLevel(score: number): { label: string; tone: "" | "warn" | "crit" } {
  if (score >= 70) return { label: "Risco alto", tone: "crit" };
  if (score >= 35) return { label: "Risco médio", tone: "warn" };
  return { label: "Risco baixo", tone: "" };
}

/** Valor de um campo na comparação de versões: "—" quando não existia, texto legível para listas/objetos. */
export function showValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v === "" ? "(vazio)" : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/** Caminho do campo na comparação em linguagem de gente: "pictures[0].source" → "Imagens 1 · source". */
export function pathLabel(path: string): string {
  return path.split(".").map((seg) => {
    const m = seg.match(/^([^[\]]+)((?:\[\d+\])*)$/);
    if (!m) return seg;
    const idx = [...m[2]!.matchAll(/\[(\d+)\]/g)].map((x) => Number(x[1]) + 1).join(".");
    return `${PAYLOAD_LABEL[m[1]!] ?? m[1]}${idx ? ` ${idx}` : ""}`;
  }).join(" · ");
}
