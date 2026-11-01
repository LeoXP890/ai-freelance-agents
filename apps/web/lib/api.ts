import type { EligibilityRow } from "./seller";
import type { Accessory, OfficeEventV1, PresenceSnapshotV1 } from "./contract";

const BUILD_API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const SERVER_KEY = "aic.serverUrl";

/** Servidor aceito: https em qualquer host; http SÓ local (localhost/127.0.0.1) — nunca tráfego remoto sem TLS. */
export function serverUrlAllowed(url: string): boolean {
  try {
    const u = new URL(url);
    if (u.protocol === "https:") return true;
    return u.protocol === "http:" && (u.hostname === "localhost" || u.hostname === "127.0.0.1");
  } catch { return false; }
}

/** Endereço da API: no desktop é configurável em tempo de execução (VPS), sem recompilar o app. */
export function apiUrl(): string {
  try {
    const v = typeof window !== "undefined" ? window.localStorage.getItem(SERVER_KEY) : null;
    if (v && serverUrlAllowed(v)) return v.replace(/\/$/, "");
  } catch { /* storage indisponível */ }
  return BUILD_API_URL;
}

/** @deprecated use apiUrl(); mantido para compatibilidade. */
export const API_URL = BUILD_API_URL;

export class ApiError extends Error {
  constructor(public status: number, message: string, public code: string | null = null) {
    super(message);
  }
}

/** Normaliza `detail` do FastAPI: string, {code, message} ou lista de erros de validação. */
export function parseDetail(detail: unknown, fallback: string): { message: string; code: string | null } {
  if (typeof detail === "string") return { message: detail, code: null };
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)));
    return { message: msgs.join("; ") || fallback, code: "VALIDATION" };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; code?: unknown };
    return { message: typeof d.message === "string" ? d.message : fallback,
             code: typeof d.code === "string" ? d.code : null };
  }
  return { message: fallback, code: null };
}

async function call<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${apiUrl()}${path}`, { ...init, headers: { ...headers, ...(init.headers as object) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const { message, code } = parseDetail(body.detail, res.statusText || `HTTP ${res.status}`);
    throw new ApiError(res.status, message, code);
  }
  return body as T;
}

export type LoginResult =
  | { mfa_required: false; access_token: string }
  | { mfa_required: true; mfa_enrollment_required: false; challenge_token: string }
  | { mfa_required: true; mfa_enrollment_required: true; enrollment_token: string };

export interface MfaEnrollment {
  secret: string; otpauth_uri: string; qr_svg_data_uri: string; issuer: string; account: string;
}

export interface CampaignRow {
  id: string; name: string; status: string; external_id: string | null; provider: string; simulated: boolean;
  daily_budget_minor: number; cap_minor: number; spend_minor: number; cap_used_bps: number;
  impressions: number; clicks: number; conversions: number; revenue_minor: number;
  ctr_bps: number | null; cpc_minor: number | null; roas_x100: number | null;
  ledger_spend_minor: number; reconciled: boolean; pause_proposal_id: string | null; created_at: string;
  days: { day: string; impressions: number; clicks: number; conversions: number; revenue_minor: number;
    spend_minor: number; source: string }[];
}

export interface StopReport {
  active: boolean; message?: string; reason?: string; activated_at?: string; released_at?: string;
  revoked_delegations?: string[];
  pauses?: { kind: string; subject_id: string; channel: string; proposal_id: string | null;
    proposal_status: string | null; job_status: string | null; outcome: "CONFIRMADA" | "PENDENTE" | "FALHOU" }[];
  pause_counts?: Record<string, number>; all_paused?: boolean;
  frozen_jobs?: { action_type: string; status: string }[]; spend_since_stop_minor?: number; note?: string;
}

export interface UserRow {
  id: string; email: string; display_name: string; roles: string[]; active: boolean; mfa_enabled: boolean;
  mfa_required: boolean; locked: boolean; simulated: boolean; created_at: string;
}

export interface Me { email: string; roles: string[]; permissions: string[]; mfa: boolean; simulated: boolean }

export interface AgentTask {
  id: string; agent_id: string; kind: string; state: string; simulated: boolean;
  input?: Record<string, unknown>;
  result: Record<string, unknown> | null; error: string | null; created_at: string | null;
}

export interface Proposal {
  id: string; action_type: string; agent_id: string | null; subject_type: string; subject_id: string;
  subject_version: number; payload_sha256: string; risk_score: number; evidence_refs: string[];
  monetary_scope_minor: number; currency: string; mode: string; simulated: boolean; policy_version: string;
  policy_decision: string; policy_reasons: string[]; status: string; expires_at: string; created_at: string | null;
  /** Resumo derivado da própria proposta (objeto afetado, canal, motivo); campos ausentes vêm null. */
  summary?: { object: string | null; detail: string | null; channel: string | null; reason: string | null };
}

export interface ApprovalDetail extends Proposal {
  payload: Record<string, unknown>;
  go_live_context: Record<string, string> | null;
  budget_account_id: string | null;
  created_by: string | null;
  executable: boolean;
  /** O que muda em relação ao que já foi executado para o mesmo assunto (ou à proposta anterior). */
  comparison: { base: { id: string; action_type: string; status: string; subject_version: number; created_at: string; executed: boolean };
    changes: { path: string; before: unknown; after: unknown }[]; truncated: boolean } | null;
  decisions: { decision: string; decided_by: string; comment: string; decided_at: string }[];
  grant: { id: string; expires_at: string; consumed: boolean; revoked: boolean; reservation_id: string | null } | null;
  job: { id: string; status: string; attempts: number; provider: string | null; provider_ref: string | null;
         error_kind: string | null; last_error: string | null } | null;
}

export type DecisionAction = "approve" | "reject" | "request-changes";

export interface ProposalInput {
  action_type: string; agent_id?: string; subject_type: string; subject_id: string; subject_version: number;
  payload: Record<string, unknown>; risk: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"; evidence_refs?: string[];
  monetary_scope_minor?: number; budget_account_id?: string; idempotency_key: string;
  go_live?: { channel: string; supplier_origin: string; fulfillment_mode: string; uf: string };
}

export interface ChecklistItem { key: string; label: string; ok: boolean; detail: string }
export interface SupplierSummary {
  id: string; name: string; legal_id: string | null; origin: string; country: string; status: string;
  sla_dispatch_days: number | null; sample_status: string; version: number; simulated: boolean; note: string;
  assessment: { score: number; level: string; checklist: ChecklistItem[]; ready_for_approval: boolean; stale_offers: number };
}
export interface Offer {
  id: string; supplier_id: string; variant_id: string; cost_minor: number; currency: string; moq: number;
  lead_time_days: number; stock_qty: number; stock_observed_at: string; status: string; source_ref: string;
}
export interface SupplierDetail extends SupplierSummary {
  documents: { id: string; doc_type: string; reference: string; verified: boolean }[];
  offers: Offer[];
}
export interface ComplianceIssue { severity: "BLOCKER" | "WARNING"; code: string; message: string }
export interface Listing {
  id: string; channel: string; status: string; version: number; variant_id: string; sku: string | null;
  title: string | null; offer_id: string; price_minor: number; currency: string; compliance: ComplianceIssue[];
  blockers: ComplianceIssue[]; external_id: string | null; proposal_id: string | null; simulated: boolean;
  pricing: { quote: { breakeven_minor: number | null; suggested_minor: number | null; warnings: string[];
    breakdown: { contribution_minor: number; margin_bps: number; fixed_minor: number; tax_minor?: number; returns_minor?: number;
      commission_minor?: number; payment_fee_minor?: number; price_minor?: number } | null };
    input?: { cost_minor: number; shipping_minor: number; returns_bps: number; ads_cpa_minor: number; target_margin_bps: number } };
  fulfillment_mode?: string; uf?: string;
}
export interface ProductSummary {
  id: string; title: string; status: string; simulated: boolean;
  variants: { id: string; sku: string; attributes: Record<string, string> }[];
}

export interface ProductFull extends ProductSummary {
  sku_base: string; description: string; category: string; brand: string | null; ncm: string | null;
  regulated: boolean; images: string[]; media: { url: string; alt: string }[]; version: number;
}

export interface OrderSummary {
  id: string; channel: string; external_id: string; status: string; payment_status: string; fiscal_status: string;
  total_minor: number; currency: string; uf: string | null; placed_at: string; delivered_at: string | null;
  incident_reason: string | null; simulated: boolean; version: number;
}
export interface OrderDetail extends OrderSummary {
  items: { sku: string; qty: number; unit_price_minor: number; unit_cost_minor: number | null }[];
  events: { from: string | null; to: string; source: string; at: string }[];
  fulfillments: { id: string; status: string; cost_minor: number; carrier: string | null; tracking_code: string | null;
    promised_by: string; proposal_id: string | null }[];
  fiscal_documents: { id: string; kind: string; status: string; environment: string; access_key: string | null; reason: string | null }[];
  returns: { id: string; reason: string; status: string; withdrawal_right: boolean; refund_minor: number }[];
  customer: Record<string, string | null>;
}
export interface Alert { severity: "CRITICAL" | "WARNING"; kind: string; message: string; ref: string }
export interface ReconRow { order_id: string; channel: string; order_external_id: string; expected_net_minor: number;
  settled_net_minor: number; diff_minor: number; kinds: string[]; note?: string }
export interface Reconciliation {
  totals: { matched: number; divergent: number; orphans: number; receivable_open_minor: number; divergence_minor: number;
    disputes?: number; at_risk_minor?: number };
  divergent: ReconRow[];
  matched?: ReconRow[];
  orphans?: { channel: string; order_external_id: string; net_minor: number; kind: string }[];
  disputes?: { order_id: string; channel: string; order_external_id: string; at_risk_minor: number; settled_net_minor: number }[];
  open_receivables?: { order_id: string; channel: string; order_external_id: string; expected_net_minor: number; days: number; overdue: boolean }[];
  note: string;
}
export interface BudgetAccount { code: string; name: string; account_id: string; currency: string; approved_cap_minor: number;
  reconciled_spend_minor: number; outstanding_reservations_minor: number; safety_hold_minor: number; available_minor: number; simulated: boolean }

export interface AiSpec { kind: string; agent_id: string; title: string; tier: string }
export interface ChatReply {
  conversation_id: string; route: string; answer: string; model: string; tier: string; cost_micro_usd: number;
  llm_simulated: boolean; sources: { id: string; title: string; url: string; approval_status: string }[];
  facts: Record<string, unknown>; suggested_action: { action_type: string; description: string };
}
export type MissionBlocker = "WAITING_APPROVAL" | "WAITING_TOOL" | "BLOCKED_POLICY" | "RECOVERABLE" | "FAILED";
export interface MissionRow {
  id: string; state: string; goal: string; max_steps: number | null; rerun_of: string | null; simulated: boolean;
  error: string | null; error_kind: string | null; blocker: MissionBlocker | null; agents: string[];
  steps_total: number; steps_done: number; steps_failed: number; cost_micro_usd: number;
  created_at: string; updated_at: string; duration_s: number;
}
export interface Mission extends MissionRow {
  result: { summary?: string; plan?: { kind: string; why: string }[]; report?: Record<string, unknown> } | null;
  children: { id: string; agent_id: string; kind: string; state: string; why?: string | null; summary: string | null;
    error: string | null; error_kind?: string | null; attempts?: number; cost_micro_usd?: number;
    created_at?: string; updated_at?: string }[];
  timeline?: { at: string; seq: number; task_id: string; agent_id: string | null; type: string; task_state: string | null;
    from: string | null; tool: string | null; detail: Record<string, unknown> }[];
  checkpoints?: { at: string; task_id: string; node: string }[];
}
export interface KbDoc {
  id: string; collection: string; title: string; source_url: string; publisher: string; document_type: string;
  version: number; approval_status: string; suspicious: boolean; access_policy: string[]; valid_until: string | null;
  verified_at: string | null; published_at: string | null; created_at: string | null; expired: boolean; owner: string;
  owner_email: string | null; jurisdiction: string | null; channel: string | null; simulated: boolean;
}
export interface KbHit { document_id: string; title: string; collection: string; url: string; approval_status: string;
  score: number; text: string }
export interface AuditRow {
  id: number; occurred_at: string; actor_type: string; actor_id: string; actor_label: string; action: string;
  subject_type: string; subject_id: string; mode: string; payload: Record<string, unknown>;
}
export interface OpportunityRow {
  id: string; title: string; category: string; rationale: string; score: number; evidence_quality: string;
  sources: { id: string; title: string; url: string; date: string | null; simulated: boolean; market?: MarketEvidence }[];
  contraindications: string[]; status: string; simulated: boolean;
}
/** Evidência real do Mercado Livre (pesquisa somente leitura): fotos do catálogo, preços concorrentes e tarifa oficial. */
export interface MarketEvidence {
  product_id: string; permalink: string | null; domain_id: string; brand: string | null; position: number;
  sellers: number; price_min: number | null; price_median: number | null; price_max: number | null;
  category_id: string | null; pictures: string[]; trend_match: string | null;
  shipping?: { seller_cost: number; billable_weight_g: number | null; box: { cm: number[]; grams: number; source: string } } | null;
  max_supplier_and_tax?: number | null;
  fee: { listing_type: string; sale_fee_amount: number | null; percentage_fee: number | null; fixed_fee: number | null } | null;
}
export interface AiUsage {
  month_spend_micro_usd: number; monthly_budget_micro_usd: number; provider: string;
  by_agent_model: { agent_id: string; model: string; calls: number; cost_micro_usd: number; simulated: boolean }[];
}

export interface SellerProfile {
  person_type: "PERSON" | "COMPANY"; person_type_label: string; country: string; uf: string | null;
  tax_id_status: string; tax_id_note: string; fiscal_model: string; fiscal_model_label: string; legal_review: string;
  restrictions: string[]; verified_at: string | null; version: number; fiscal_models: Record<string, string>; note: string;
}
export interface GateRow { code: string; title: string; state: string; responsible: string; blocks: string;
  valid_until: string | null; approved_for?: string[] | null; expected_evidence?: string }
export interface RoutineKind { kind: string; agent_id: string; label: string; field: string | null; field_label: string | null }
export interface RoutineInput {
  agent_id: string; kind: string; schedule: "DAILY" | "INTERVAL"; time_of_day?: string | null;
  interval_hours?: number | null; text?: string | null; title?: string | null;
}
export interface Routine {
  id: string; agent_id: string; kind: string; title: string; text: string | null; schedule: "DAILY" | "INTERVAL";
  time_of_day: string | null; interval_hours: number | null; enabled: boolean; next_run_at: string | null;
  last_run_at: string | null; last_task_id: string | null; last_outcome: string | null;
}
export interface GateDecision {
  decision: "APPROVED" | "APPROVED_WITH_RESTRICTIONS" | "REJECTED";
  scope: Record<string, unknown>; valid_until: string | null; note: string;
}
export interface GateDetail extends GateRow {
  expected_evidence: string; scope: Record<string, unknown>; approved_by: string | null; evidence_sha256: string | null;
  seller_type?: string; requirements?: { label: string; met: boolean }[];
  evidence: { id: string; kind: string; reference: string; sha256: string; submitted_at: string }[];
}
export interface SalesDashboard {
  days: number; simulated: boolean; note: string;
  totals: { orders: number; net_revenue_minor: number; contribution_minor: number };
  channels: { channel: string; orders: number; revenue_minor: number; net_revenue_minor: number;
    contribution_minor: number; avg_ticket_minor: number; cancel_rate_bps: number; margin_bps: number;
    refunded: number; cancelled?: number; refund_minor?: number; cost_minor?: number; fees_minor?: number; cost_known?: boolean }[];
  daily: { date: string; revenue_minor: number }[];
}

export const api = {
  setAppearance: (t: string, agentId: string, color: string, accessory: string, skin: string) =>
    call<{ id: string; color: string; appearance: { accessory: Accessory; skin: string } }>(
      `/v1/agents/${agentId}/appearance`, t, { method: "PUT", body: JSON.stringify({ color, accessory, skin }) }),
  gates: (t: string) => call<{ items: GateRow[]; seller_type?: string }>("/v1/gates", t),
  sellerProfile: (t: string) => call<SellerProfile>("/v1/seller/profile", t),
  updateSellerProfile: (t: string, body: Partial<Pick<SellerProfile, "person_type" | "uf" | "fiscal_model">>) =>
    call<SellerProfile & { gates_lost: string[] }>("/v1/seller/profile", t, { method: "PUT", body: JSON.stringify(body) }),
  eligibility: (t: string) => call<{ seller_type: string; aspects: Record<string, string>; items: EligibilityRow[] }>("/v1/seller/eligibility", t),
  gate: (t: string, code: string) => call<GateDetail>(`/v1/gates/${code}`, t),
  gateEvidence: (t: string, code: string, kind: string, reference: string, notes: string) =>
    call<GateDetail>(`/v1/gates/${code}/evidence`, t, { method: "POST", body: JSON.stringify({ kind, reference, notes }) }),
  gateSubmit: (t: string, code: string) => call<GateDetail>(`/v1/gates/${code}/submit`, t, { method: "POST" }),
  routines: (t: string, agentId?: string) =>
    call<{ items: Routine[]; kinds: RoutineKind[] }>(`/v1/agent-routines${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`, t),
  createRoutine: (t: string, body: RoutineInput) =>
    call<Routine>("/v1/agent-routines", t, { method: "POST", body: JSON.stringify(body) }),
  updateRoutine: (t: string, id: string, body: Partial<RoutineInput> & { enabled?: boolean }) =>
    call<Routine>(`/v1/agent-routines/${id}`, t, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRoutine: (t: string, id: string) => call<void>(`/v1/agent-routines/${id}`, t, { method: "DELETE" }),
  runRoutine: (t: string, id: string) => call<AgentTask>(`/v1/agent-routines/${id}/run`, t, { method: "POST" }),
  gateDecide: (t: string, code: string, body: GateDecision) =>
    call<GateDetail>(`/v1/gates/${code}/decide`, t, { method: "POST", body: JSON.stringify(body) }),
  updateEligibility: (t: string, channel: string, body: unknown) =>
    call<unknown>(`/v1/seller/eligibility/${channel}`, t, { method: "PUT", body: JSON.stringify(body) }),
  salesDashboard: (t: string, days = 30) => call<SalesDashboard>(`/v1/dashboard/sales?days=${days}`, t),
  aiSpecs: (t: string) => call<{ items: AiSpec[]; provider: string; models: Record<string, string> }>("/v1/ai/specs", t),
  conversations: (t: string, agentId?: string) =>
    call<{ items: { id: string; agent_id: string; agents: string[]; title: string; started_at: string; last_at: string; messages: number; cost_micro_usd: number }[] }>(
      `/v1/conversations${agentId ? `?agent_id=${agentId}` : ""}`, t),
  conversation: (t: string, id: string, before_id?: number) =>
    call<{ id: string; agent_id: string | null; next_before_id: number | null;
      items: { id: number; agent_id: string; role: string; content: string; meta: Record<string, unknown>; at: string }[] }>(
      `/v1/conversations/${id}${before_id ? `?before_id=${before_id}` : ""}`, t),
  chat: (t: string, agentId: string, message: string, conversation_id?: string) =>
    call<ChatReply>(`/v1/agents/${agentId}/chat`, t,
      { method: "POST", body: JSON.stringify({ message, conversation_id }) }),
  createMission: (t: string, goal: string, max_steps = 3) =>
    call<{ mission_id: string }>("/v1/missions", t, { method: "POST", body: JSON.stringify({ goal, max_steps }) }),
  mission: (t: string, id: string) => call<Mission>(`/v1/missions/${id}`, t),
  missions: (t: string, state?: "OPEN" | "DONE") =>
    call<{ items: MissionRow[] }>(`/v1/missions${state ? `?state=${state}` : ""}`, t),
  cancelMission: (t: string, id: string, reason: string) =>
    call<Mission>(`/v1/missions/${id}/cancel`, t, { method: "POST", body: JSON.stringify({ reason }) }),
  rerunMission: (t: string, id: string) =>
    call<{ mission_id: string; rerun_of: string }>(`/v1/missions/${id}/rerun`, t, { method: "POST" }),
  kbDocuments: (t: string, f: { collection?: string; approval_status?: string; q?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => !!v) as [string, string][]).toString();
    return call<{ items: KbDoc[]; collections: string[] }>(`/v1/knowledge/documents${qs ? `?${qs}` : ""}`, t);
  },
  kbSearch: (t: string, q: string, collection?: string) =>
    call<{ route: string; items: KbHit[] }>(`/v1/knowledge/search?q=${encodeURIComponent(q)}&k=8${collection ? `&collection=${encodeURIComponent(collection)}` : ""}`, t),
  kbReview: (t: string, id: string, approved: boolean, note: string) =>
    call<KbDoc>(`/v1/knowledge/documents/${id}/review`, t, { method: "POST", body: JSON.stringify({ approved, note }) }),
  auditEvents: (t: string, f: { action?: string; subject_id?: string; before_id?: number; limit?: number } = {}) => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)])).toString();
    return call<{ items: AuditRow[]; next_before_id: number | null }>(`/v1/audit/events${qs ? `?${qs}` : ""}`, t);
  },
  auditVerify: (t: string) => call<{ intact: boolean; first_invalid_id: number | null }>("/v1/audit/verify", t),
  opportunities: (t: string) => call<{ items: OpportunityRow[] }>("/v1/opportunities", t),
  opportunityProposal: (t: string, id: string) =>
    call<Proposal>(`/v1/opportunities/${id}/proposals`, t, { method: "POST" }),
  aiUsage: (t: string) => call<AiUsage>("/v1/ai/usage", t),
  orders: (t: string) => call<{ items: OrderSummary[] }>("/v1/orders", t),
  order: (t: string, id: string) => call<OrderDetail>(`/v1/orders/${id}`, t),
  orderCustomer: (t: string, id: string) => call<Record<string, string | null>>(`/v1/orders/${id}/customer`, t),
  fulfillOrder: (t: string, id: string) => call<{ proposals: Proposal[] }>(`/v1/orders/${id}/fulfill`, t, { method: "POST" }),
  cancelOrder: (t: string, id: string, reason: string) =>
    call<OrderDetail>(`/v1/orders/${id}/cancel`, t, { method: "POST", body: JSON.stringify({ reason }) }),
  openReturn: (t: string, id: string, reason: string, description: string) =>
    call<{ withdrawal_right: boolean; refund_minor: number; proposal: Proposal }>(`/v1/orders/${id}/returns`, t,
      { method: "POST", body: JSON.stringify({ reason, description }) }),
  simOrder: (t: string, listing_id: string, qty = 1) =>
    call<OrderDetail>("/v1/sim/orders", t, { method: "POST", body: JSON.stringify({ listing_id, qty, paid: true }) }),
  simShipping: (t: string, fulfillmentId: string, event: "ship" | "deliver") =>
    call<OrderDetail>(`/v1/sim/fulfillments/${fulfillmentId}/${event}`, t, { method: "POST" }),
  simSettlement: (t: string, channel: string) =>
    call<{ net_minor: number }>("/v1/sim/settlements", t, { method: "POST", body: JSON.stringify({ channel }) }),
  reconciliation: (t: string) => call<Reconciliation>("/v1/finance/reconciliation", t),
  settlements: (t: string) => call<{ items: { id: string; channel: string; external_id: string; gross_minor: number; fees_minor: number;
    net_minor: number; simulated: boolean; imported_at: string | null }[] }>("/v1/finance/settlements", t),
  channelAccounts: (t: string) => call<{ items: { id: string; channel: string; external_account_id: string; status: string;
    expires_at: string | null; simulated: boolean }[] }>("/v1/channels/accounts", t),
  /** Foto do produto: corpo = bytes do arquivo; o servidor regrava sem metadados e devolve a URL pública. */
  uploadMedia: (t: string, file: Blob) =>
    call<{ url: string; sha256: string; width: number; height: number; bytes: number }>("/v1/media", t,
      { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } }),
  oauthStart: (t: string, channel: string) =>
    call<{ authorize_url: string; expires_in_s: number }>(`/v1/channels/${channel}/oauth/start`, t, { method: "POST" }),
  alerts: (t: string) => call<{ items: Alert[]; critical: number }>("/v1/alerts", t),
  suppliers: (t: string) => call<{ items: SupplierSummary[] }>("/v1/suppliers", t),
  supplier: (t: string, id: string) => call<SupplierDetail>(`/v1/suppliers/${id}`, t),
  supplierTransition: (t: string, id: string, to: string, expected_version: number, reason: string) =>
    call<SupplierDetail>(`/v1/suppliers/${id}/transition`, t,
      { method: "POST", body: JSON.stringify({ to, expected_version, reason }) }),
  supplierDocument: (t: string, id: string, doc_type: string, reference: string) =>
    call<{ id: string }>(`/v1/suppliers/${id}/documents`, t, { method: "POST", body: JSON.stringify({ doc_type, reference }) }),
  verifySupplierDocument: (t: string, id: string, docId: string) =>
    call<SupplierDetail>(`/v1/suppliers/${id}/documents/${docId}/verify`, t, { method: "POST" }),
  supplierSample: (t: string, id: string, sample_status: string, expected_version: number) =>
    call<SupplierDetail>(`/v1/suppliers/${id}/sample`, t, { method: "POST", body: JSON.stringify({ sample_status, expected_version }) }),
  products: (t: string) => call<{ items: ProductSummary[] }>("/v1/products", t),
  product: (t: string, id: string) => call<ProductSummary & { offers: Offer[] }>(`/v1/products/${id}`, t),
  productFull: (t: string, id: string) => call<ProductFull>(`/v1/products/${id}`, t),
  updateProduct: (t: string, id: string, body: Record<string, unknown>) =>
    call<ProductFull & { changed: string[]; listings_marked: number }>(`/v1/products/${id}`, t,
      { method: "PUT", body: JSON.stringify(body) }),
  setProductArchived: (t: string, id: string, archived: boolean, reason: string) =>
    call<ProductFull>(`/v1/products/${id}/${archived ? "archive" : "reactivate"}`, t,
      { method: "POST", body: JSON.stringify({ reason }) }),
  productVersions: (t: string, id: string) =>
    call<{ items: { version: number; changed_fields: string[]; changed_by: string; at: string }[] }>(
      `/v1/products/${id}/versions`, t),
  listings: (t: string) => call<{ items: Listing[] }>("/v1/listings", t),
  prepareListing: (t: string, channel: string, body: { variant_id: string; offer_id: string; shipping_minor?: number }) =>
    call<Listing>(`/v1/channels/${channel}/listings/prepare`, t, { method: "POST", body: JSON.stringify(body) }),
  proposePublish: (t: string, listingId: string) =>
    call<Proposal>(`/v1/listings/${listingId}/propose-publish`, t, { method: "POST" }),
  proposePause: (t: string, listingId: string, reason: string) =>
    call<Proposal>(`/v1/listings/${listingId}/propose-pause`, t, { method: "POST", body: JSON.stringify({ reason }) }),
  approvals: (t: string, status?: string) =>
    call<{ items: Proposal[] }>(`/v1/approvals${status ? `?status=${encodeURIComponent(status)}` : ""}`, t),
  approval: (t: string, id: string) => call<ApprovalDetail>(`/v1/approvals/${id}`, t),
  decide: (t: string, id: string, action: DecisionAction,
           body: { payload_sha256: string; subject_version: number; comment: string; idempotency_key: string }) =>
    call<ApprovalDetail>(`/v1/approvals/${id}/${action}`, t, { method: "POST", body: JSON.stringify(body) }),
  revoke: (t: string, id: string, reason: string, idempotency_key: string) =>
    call<ApprovalDetail>(`/v1/approvals/${id}/revoke`, t,
      { method: "POST", body: JSON.stringify({ reason, idempotency_key }) }),
  propose: (t: string, body: ProposalInput) =>
    call<Proposal>("/v1/proposals", t, { method: "POST", body: JSON.stringify(body) }),
  changePassword: (t: string, current_password: string, new_password: string) =>
    call<{ access_token: string }>("/v1/auth/password", t,
      { method: "POST", body: JSON.stringify({ current_password, new_password }) }),
  login: (email: string, password: string) =>
    call<LoginResult>("/v1/auth/login", null, { method: "POST", body: JSON.stringify({ email, password }) }),
  verifyMfa: (challenge_token: string, code: string) =>
    call<{ access_token: string }>("/v1/auth/mfa/verify", null,
      { method: "POST", body: JSON.stringify({ challenge_token, code }) }),
  enrollStart: (enrollment_token: string) =>
    call<MfaEnrollment>("/v1/auth/mfa/enroll/start", null,
      { method: "POST", body: JSON.stringify({ enrollment_token }) }),
  enrollConfirm: (enrollment_token: string, code: string) =>
    call<{ access_token: string; recovery_codes: string[] }>("/v1/auth/mfa/enroll/confirm", null,
      { method: "POST", body: JSON.stringify({ enrollment_token, code }) }),
  recoverMfa: (challenge_token: string, recovery_code: string) =>
    call<{ access_token: string; recovery_codes_left: number }>("/v1/auth/mfa/recover", null,
      { method: "POST", body: JSON.stringify({ challenge_token, recovery_code }) }),
  recoveryStatus: (t: string) => call<{ mfa_enabled: boolean; remaining: number }>("/v1/auth/mfa/recovery-codes", t),
  regenerateRecovery: (t: string, code: string) =>
    call<{ recovery_codes: string[] }>("/v1/auth/mfa/recovery-codes", t, { method: "POST", body: JSON.stringify({ code }) }),
  fiscalDocuments: (t: string, orderId: string) =>
    call<{ items: { id: string; kind: string; status: string; access_key: string | null; provider: string;
      issued_at: string | null; simulated: boolean }[] }>(`/v1/orders/${orderId}/fiscal-documents`, t),
  campaigns: (t: string) => call<{ items: CampaignRow[] }>("/v1/campaigns", t),
  simCampaignDays: (t: string, id: string, days = 1) =>
    call<{ imported: number }>(`/v1/sim/campaigns/${id}/metrics`, t, { method: "POST", body: JSON.stringify({ days }) }),
  pauseCampaign: (t: string, id: string, reason: string) =>
    call<{ id: string; status: string }>(`/v1/campaigns/${id}/pause`, t, { method: "POST", body: JSON.stringify({ reason }) }),
  stopReport: (t: string) => call<StopReport>("/v1/emergency-stop/report", t),
  users: (t: string) => call<{ items: UserRow[]; roles: Record<string, string> }>("/v1/users", t),
  createUser: (t: string, email: string, display_name: string, roles: string[]) =>
    call<UserRow & { temporary_password: string }>("/v1/users", t,
      { method: "POST", body: JSON.stringify({ email, display_name, roles }) }),
  setUserRoles: (t: string, id: string, roles: string[]) =>
    call<UserRow>(`/v1/users/${id}/roles`, t, { method: "PUT", body: JSON.stringify({ roles }) }),
  userAction: (t: string, id: string, action: "deactivate" | "activate" | "unlock" | "reset-mfa") =>
    call<UserRow>(`/v1/users/${id}/${action}`, t, { method: "POST" }),
  me: (t: string) => call<Me>("/v1/auth/me", t),
  snapshot: (t: string) => call<PresenceSnapshotV1>("/v1/agent-presence/snapshot", t),
  eventsAfter: (t: string, after: number) =>
    call<{ items: OfficeEventV1[]; next_after_seq: number }>(`/v1/office/events?after_seq=${after}`, t),
  devices: (t: string) => call<{ items: { id: string; name: string; created_at: string; last_used_at: string | null; revoked: boolean }[] }>("/v1/auth/devices", t),
  revokeDevice: (t: string, id: string) => call<{ id: string; revoked: boolean }>(`/v1/auth/devices/${id}/revoke`, t, { method: "POST" }),
  recentTasks: (t: string, limit = 30) => call<{ items: AgentTask[] }>(`/v1/agent-tasks?limit=${limit}`, t),
  emergencyStop: (t: string, reason: string) =>
    call<Record<string, unknown>>("/v1/emergency-stop", t, { method: "POST", body: JSON.stringify({ reason }) }),
  systemInfo: () => call<{ version: string; environment: string; mode: string; simulated: boolean; notice: string }>("/v1/system/info", null),
  tasks: (t: string, agentId: string) => call<{ items: AgentTask[] }>(`/v1/agent-tasks?agent_id=${agentId}&limit=5`, t),
  createTask: (t: string, agent_id: string, kind: string, input: Record<string, unknown> = {}) =>
    call<AgentTask>("/v1/agent-tasks", t, { method: "POST", body: JSON.stringify({ agent_id, kind, input }) }),
  finance: (t: string) => call<{ accounts: Array<Record<string, unknown>> }>("/v1/finance/overview", t),
};

export function wsUrl(): string {
  const u = apiUrl();
  const base = u.startsWith("http") ? u : `${window.location.origin}${u}`;
  return base.replace(/^http/, "ws") + "/v1/office/events";
}
