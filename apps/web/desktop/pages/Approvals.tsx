"use client";
// UX-05 Central de Aprovações (UXD-011): inbox priorizada (urgente / risco / valor) + dossiê da proposta.
// A decisão envia o payload_sha256 e a versão EXIBIDOS (se a proposta mudou, o backend recusa), usa chave de
// idempotência por decisão e só aparece como feita depois da resposta do servidor. Nada de "aprovar tudo".
import { AlertTriangle, Check, Clock, FileSearch, Inbox, MessageSquareWarning, Search, ShieldAlert, Undo2, Wallet, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type ApprovalDetail, type DecisionAction, type Proposal } from "@/lib/api";
import { since } from "@/lib/agentIdentity";
import { ACTION_LABEL, approvalErrorMessage, formatMinor, shortHash, STATUS_LABEL } from "@/lib/format";
import { approvalPriority, economicsOf, expiresIn, marketCheckOf, pathLabel, payloadRows, riskLevel, showValue, sortInbox } from "@/lib/workflow";
import { channelName } from "./Finance";
import AgentPortrait from "../AgentPortrait";
import { useDesk } from "../context";
import { openExternal } from "../native";
import { Skeleton } from "../table";
import { ConfirmDialog, Empty } from "../ui";
import { useAgentName } from "./Missions";

type Tab = "PENDING" | "DECIDED" | "ALL";
type Pri = "all" | "urgent" | "risk" | "value";
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const STATUS_TONE: Record<string, string> = {
  PENDING_APPROVAL: "warn", AUTHORIZED: "info", AUTO_AUTHORIZED: "info", EXECUTING: "info", SUCCEEDED: "ok", FAILED: "crit",
  UNKNOWN: "warn", REJECTED: "crit", CHANGES_REQUESTED: "warn", DENIED: "crit", REVOKED: "muted", EXPIRED: "muted",
};
const DECISION_LABEL: Record<string, string> = { APPROVE: "Aprovou", REJECT: "Rejeitou", REQUEST_CHANGES: "Pediu mudanças", REVOKE: "Revogou" };

export function StatusChip({ status }: { status: string }) {
  return <span className={`dk-chip ${STATUS_TONE[status] ?? ""}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export default function Approvals() {
  const desk = useDesk();
  const [tab, setTab] = useState<Tab>("PENDING");
  const [pri, setPri] = useState<Pri>("all");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(desk.approvalFocus?.id ?? null);
  useEffect(() => { if (desk.approvalFocus) setSelected(desk.approvalFocus.id); }, [desk.approvalFocus]);
  const nameOf = useAgentName();

  const load = useCallback(() => {
    api.approvals(desk.token, tab === "PENDING" ? "PENDING_APPROVAL" : undefined)
      .then((r) => { setItems(r.items); setError(null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha de conexão com a API"));
  }, [desk.token, tab]);
  useEffect(() => { load(); }, [load, desk.approvalsSignal, desk.refresh]);

  const now = Date.now();
  const list = useMemo(() => {
    let xs = items ?? [];
    if (tab === "DECIDED") xs = xs.filter((p) => p.status !== "PENDING_APPROVAL");
    if (pri !== "all") xs = xs.filter((p) => approvalPriority(p, now)[pri]);
    if (q) {
      const t = q.toLowerCase();
      xs = xs.filter((p) => `${ACTION_LABEL[p.action_type] ?? p.action_type} ${p.agent_id ?? ""} ${nameOf(p.agent_id ?? "")} ${p.subject_type} ${p.subject_id} ${p.summary?.object ?? ""} ${p.summary?.detail ?? ""} ${p.summary?.channel ?? ""} ${p.summary?.reason ?? ""}`.toLowerCase().includes(t));
    }
    return sortInbox(xs, now);
  }, [items, tab, pri, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const pending = (items ?? []).filter((p) => p.status === "PENDING_APPROVAL");
  const counts = {
    urgent: pending.filter((p) => approvalPriority(p, now).urgent).length,
    risk: pending.filter((p) => approvalPriority(p, now).risk).length,
    value: pending.filter((p) => approvalPriority(p, now).value).length,
  };

  return (
    <div className="page split-page">
      <div className="page-head">
        <div>
          <h1>Aprovações</h1>
          <p>Propostas dos agentes que dependem da sua decisão. Nada é executado sem a confirmação do servidor.</p>
        </div>
      </div>
      <div className="filters">
        <div className="dk-seg" role="group" aria-label="Situação">
          {([["PENDING", `Pendentes${tab === "PENDING" && items ? ` (${items.length})` : ""}`], ["DECIDED", "Decididas"], ["ALL", "Todas"]] as [Tab, string][]).map(([k, l]) => (
            <button key={k} type="button" aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
        <div className="dk-seg" role="group" aria-label="Prioridade">
          <button type="button" aria-pressed={pri === "all"} onClick={() => setPri("all")}>Todas</button>
          <button type="button" aria-pressed={pri === "urgent"} onClick={() => setPri("urgent")}><Clock size={14} aria-hidden="true" /> Urgentes{counts.urgent ? ` · ${counts.urgent}` : ""}</button>
          <button type="button" aria-pressed={pri === "risk"} onClick={() => setPri("risk")}><ShieldAlert size={14} aria-hidden="true" /> Risco{counts.risk ? ` · ${counts.risk}` : ""}</button>
          <button type="button" aria-pressed={pri === "value"} onClick={() => setPri("value")}><Wallet size={14} aria-hidden="true" /> Com valor{counts.value ? ` · ${counts.value}` : ""}</button>
        </div>
        <label className="search-field"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" placeholder="Buscar ação, agente ou assunto" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar propostas" />
        </label>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}

      <div className="split inbox-split with-detail">
        <section className="dk-card inbox-list" aria-label="Caixa de propostas">
          {items === null ? <Skeleton /> : list.length === 0 ? (
            <Empty icon={<Inbox />} title={tab === "PENDING" && pri === "all" && !q ? "Nada aguardando sua decisão" : "Nenhuma proposta com esse filtro"}>
              Quando um agente propuser publicar, gastar ou reembolsar, a proposta aparece aqui com o dossiê.
            </Empty>
          ) : (
            <ul className="inbox-ul">
              {list.map((p) => {
                const pr = approvalPriority(p, now);
                return (
                  <li key={p.id}>
                    <button type="button" className="inbox-row" aria-current={selected === p.id ? "true" : undefined} onClick={() => setSelected(p.id)}>
                      <AgentPortrait agentId={p.agent_id ?? "atlas"} size={34} />
                      <span className="grow" style={{ minWidth: 0 }}>
                        <span className="inbox-top">
                          <strong className="ellipsis">{ACTION_LABEL[p.action_type] ?? p.action_type}{p.summary?.object ? <span className="obj"> · {p.summary.object}</span> : null}</strong>
                          {p.monetary_scope_minor > 0 && <span className="num money">{formatMinor(p.monetary_scope_minor, p.currency)}</span>}
                        </span>
                        <span className="faint ellipsis" style={{ display: "block" }}>
                          {p.agent_id ? nameOf(p.agent_id) : "Humano"}{p.summary?.channel ? ` · ${channelName(p.summary.channel)}` : ""} · {since(p.created_at)}{p.summary?.detail ? ` · ${p.summary.detail}` : ""}</span>
                        {p.summary?.reason && <span className="ellipsis reason" style={{ display: "block" }}>Motivo: {p.summary.reason}</span>}
                        <span className="chip-row tight">
                          <StatusChip status={p.status} />
                          {pr.urgent && <span className="dk-chip crit"><Clock aria-hidden="true" />Urgente · {expiresIn(p.expires_at, now)}</span>}
                          {p.risk_score > 0 && <span className={`dk-chip ${riskLevel(p.risk_score).tone}`} title={`escore ${p.risk_score}/100`}><ShieldAlert aria-hidden="true" />{riskLevel(p.risk_score).label}</span>}
                          {p.simulated && <span className="dk-chip demo">Simulado</span>}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        {selected ? <ApprovalDossier id={selected} onClose={() => setSelected(null)} onDecided={load} /> : (
          <section className="dk-card detail-pane placeholder" aria-label="Detalhe da proposta">
            <Empty icon={<FileSearch />} title="Selecione uma proposta">Você verá o dossiê, as evidências, o valor e o que exatamente será executado.</Empty>
          </section>
        )}
      </div>
    </div>
  );
}

function ApprovalDossier({ id, onClose, onDecided }: { id: string; onClose(): void; onDecided(): void }) {
  const desk = useDesk();
  const nameOf = useAgentName();
  const [d, setD] = useState<ApprovalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<DecisionAction | "revoke" | null>(null);
  const [budget, setBudget] = useState<{ name: string; available: number; cap: number } | null>(null);
  const [raw, setRaw] = useState(false);
  const keys = useRef<Record<string, string>>({});
  const canDecide = desk.can("approvals:decide");

  const load = useCallback(() => {
    api.approval(desk.token, id).then((r) => { setD(r); setError(null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha de conexão com a API"));
  }, [desk.token, id]);
  useEffect(() => { setD(null); setConfirming(null); setRaw(false); load(); }, [load]);
  useEffect(() => { if (d) load(); }, [desk.approvalsSignal]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setBudget(null);
    if (!d?.budget_account_id || !desk.can("finance:read")) return;
    api.finance(desk.token).then((r) => {
      const a = r.accounts.find((x) => x.account_id === d.budget_account_id) as Record<string, unknown> | undefined;
      if (a) setBudget({ name: String(a.name), available: Number(a.available_minor), cap: Number(a.approved_cap_minor) });
    }).catch(() => undefined);
  }, [d?.budget_account_id, desk.token]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(action: DecisionAction | "revoke", comment: string) {
    if (!d) return;
    const slot = `${d.id}:${action}`;
    keys.current[slot] ??= newKey(); // mesma chave em repetições (duplo clique/rede instável) → replay no backend
    try {
      const updated = action === "revoke"
        ? await api.revoke(desk.token, d.id, comment, keys.current[slot]!)
        : await api.decide(desk.token, d.id, action, { payload_sha256: d.payload_sha256, subject_version: d.subject_version, comment, idempotency_key: keys.current[slot]! });
      setD(updated); setConfirming(null); onDecided(); desk.refreshApprovals();
      desk.notify(`Decisão registrada no servidor: ${STATUS_LABEL[updated.status] ?? updated.status}.`, updated.status === "REJECTED" ? "warn" : "ok");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === "INVALID_STATE" || e.code === "STALE_PAYLOAD") load();
        throw new Error(approvalErrorMessage(e.code, e.message));
      }
      throw new Error("Falha de conexão — tente de novo (a mesma decisão não será duplicada).");
    }
  }

  const now = Date.now();
  const pr = d ? approvalPriority(d, now) : null;
  const pending = d?.status === "PENDING_APPROVAL";
  // sem job (ex.: publicação pelo robô do Dropi) também pode ser revogada enquanto não foi executada
  const revocable = d?.status === "AUTHORIZED" && (!d.job || ["QUEUED", "RETRY"].includes(d.job.status));
  return (
    <aside className="dk-card detail-pane" aria-label="Dossiê da proposta">
      <div className="detail-head">
        <div style={{ display: "flex", gap: 12, alignItems: "center", minWidth: 0 }}>
          {d && <AgentPortrait agentId={d.agent_id ?? "atlas"} size={44} />}
          <div style={{ minWidth: 0 }}>
            <div className="faint">Proposta #{id.slice(0, 8)}{d ? ` · ${d.agent_id ? `por ${nameOf(d.agent_id)}` : "humana"} · ${since(d.created_at)}` : ""}</div>
            <h2 className="detail-title">{d ? ACTION_LABEL[d.action_type] ?? d.action_type : "Carregando…"}</h2>
          </div>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar dossiê"><X size={18} /></button>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      {d && pr && (
        <div className="detail-body">
          <div className="chip-row">
            <StatusChip status={d.status} />
            {pending && <span className={`dk-chip ${pr.urgent ? "crit" : ""}`}><Clock aria-hidden="true" />{expiresIn(d.expires_at, now)}</span>}
            <span className={`dk-chip ${pr.risk ? "warn" : ""}`}><ShieldAlert aria-hidden="true" />Risco {d.risk_score}/100</span>
            {d.simulated && <span className="dk-chip demo">Simulado (DRY_RUN)</span>}
          </div>

          <div className="facts">
            <div><span className="faint">Valor solicitado</span><strong className="num">{d.monetary_scope_minor > 0 ? formatMinor(d.monetary_scope_minor, d.currency) : "sem gasto"}</strong></div>
            <div><span className="faint">Limite disponível</span>
              <strong className="num">{budget ? formatMinor(budget.available) : d.budget_account_id ? "—" : "não reserva orçamento"}</strong>
              {budget && <span className="faint">{budget.name} · teto {formatMinor(budget.cap)}</span>}</div>
            <div><span className="faint">Validade da decisão</span><strong>{new Date(d.expires_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}</strong></div>
            <div><span className="faint">Assunto</span><strong className="ellipsis">{d.subject_type.replace(/_/g, " ")} · v{d.subject_version}</strong>
              {d.go_live_context?.channel && <span className="faint">canal {d.go_live_context.channel}</span>}</div>
          </div>
          {budget && d.monetary_scope_minor > budget.available && (
            <div className="callout crit" role="note"><AlertTriangle size={16} aria-hidden="true" /> O valor solicitado é maior que o saldo disponível da conta; o servidor recusará a reserva.</div>
          )}

          {(() => {
            const e = economicsOf(d.payload);
            if (!e) return null;
            const ok = e.profit_minor > 0;
            return (
              <section aria-label="Quanto você ganha por venda">
                <h3 className="sec-title">Quanto você ganha por venda</h3>
                <table className="dk-table" style={{ width: "100%" }}>
                  <tbody>
                    {e.lines.map((l) => (
                      <tr key={l.key} className="static">
                        <td>{l.label}<div className="faint">{l.source}</div></td>
                        <td className="num" style={{ textAlign: "right", color: l.amount_minor < 0 ? "var(--critical)" : undefined }}>
                          {l.amount_minor < 0 ? "− " : ""}{formatMinor(Math.abs(l.amount_minor), e.currency)}</td>
                      </tr>))}
                    <tr className="static">
                      <td><strong>Sobra para você por venda</strong>{e.margin_pct !== null && <div className="faint">margem {e.margin_pct.toLocaleString("pt-BR")}%</div>}</td>
                      <td className="num" style={{ textAlign: "right" }}>
                        <strong style={{ color: ok ? "var(--success)" : "var(--critical)" }}>{ok ? "" : "− "}{formatMinor(Math.abs(e.profit_minor), e.currency)}</strong></td>
                    </tr>
                  </tbody>
                </table>
                <div className="faint">Conferido nas fontes na hora da proposta. Ainda não inclui imposto (venda como pessoa física) nem anúncio pago.</div>
              </section>
            );
          })()}

          {(() => {
            const m = marketCheckOf(d.payload);
            if (!m) return null;
            return (
              <section aria-label="Concorrência">
                <h3 className="sec-title">Concorrência (pesquisa no Google)</h3>
                <div className="faint" style={{ marginBottom: 6 }}>{m.note}</div>
                <div className="rows">{m.offers.map((o) => (
                  <a key={o.url} href={o.url} className="row-item" style={{ textDecoration: "none", color: "inherit" }}
                    onClick={(e) => { e.preventDefault(); void openExternal(o.url); }}>
                    <span className="grow ellipsis"><strong>{o.store}</strong> · {o.product}</span>
                    <span className="num">{formatMinor(Math.round(o.price * 100))}{o.free_shipping ? " · frete grátis" : ""}</span>
                  </a>))}</div>
              </section>
            );
          })()}

          <section aria-label="Política">
            <h3 className="sec-title">Por que precisa de você</h3>
            <ul className="reasons-ul">{d.policy_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <div className="faint">Política {d.policy_version} → {d.policy_decision}</div>
          </section>

          <section aria-label="Mudanças propostas">
            <h3 className="sec-title">O que será executado
              <button type="button" className="dk-link" onClick={() => setRaw(!raw)} aria-expanded={raw}>{raw ? "Ver resumo" : "Ver conteúdo exato"}</button></h3>
            {raw ? <pre className="code-block">{JSON.stringify(d.payload, null, 2)}</pre> : (
              <dl className="dk-kv">{payloadRows(d.payload, d.currency).map((r) => <div key={r.key} style={{ display: "contents" }}><dt>{r.label}</dt><dd className="ellipsis" title={r.value}>{r.value}</dd></div>)}</dl>
            )}
            <div className="faint">Versão {d.subject_version} · impressão digital <code title={d.payload_sha256}>{shortHash(d.payload_sha256)}</code> — se o conteúdo mudar, o servidor recusa esta decisão.</div>
            {!d.executable && <div className="callout warn" role="note">Esta ação ainda não tem integração: se aprovada, a execução falhará como “não suportada” (nada é simulado como sucesso).</div>}
          </section>

          {d.comparison && (
            <section aria-label="Comparação com a versão anterior">
              <h3 className="sec-title">O que muda</h3>
              <p className="faint" style={{ margin: "0 0 6px" }}>
                Em relação {d.comparison.base.executed ? "ao que já está no ar" : "à proposta anterior (não executada)"} — v{d.comparison.base.subject_version}, {since(d.comparison.base.created_at)}.</p>
              {d.comparison.changes.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma diferença no conteúdo.</p> : (
                <table className="dk-table diff-table" aria-label="Diferenças">
                  <thead><tr><th>Campo</th><th>Antes</th><th>Depois</th></tr></thead>
                  <tbody>{d.comparison.changes.map((c) => (
                    <tr key={c.path} className="static"><td title={c.path}>{pathLabel(c.path)}</td>
                      <td className="ellipsis" title={showValue(c.before)}><del>{showValue(c.before)}</del></td>
                      <td className="ellipsis" title={showValue(c.after)}><ins>{showValue(c.after)}</ins></td></tr>))}</tbody>
                </table>)}
              {d.comparison.truncated && <p className="faint">Mais diferenças no conteúdo exato.</p>}
            </section>
          )}

          <section aria-label="Evidências">
            <h3 className="sec-title">Evidências ({d.evidence_refs.length})</h3>
            {d.evidence_refs.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma evidência anexada.</p> : (
              <div className="chip-row">{d.evidence_refs.map((e) => <span key={e} className="dk-chip info" title={e}>{e.split(":")[0]} · {e.split(":")[1]?.slice(0, 8)}</span>)}</div>
            )}
          </section>

          {(d.job || d.decisions.length > 0) && (
            <section aria-label="Histórico">
              <h3 className="sec-title">Histórico</h3>
              <ol className="timeline">
                {d.created_at && <li><time className="faint num">{new Date(d.created_at).toLocaleString("pt-BR")}</time><span className="dot" aria-hidden="true" /><span>Proposta criada{d.agent_id ? ` por ${nameOf(d.agent_id)}` : ""}</span></li>}
                {d.decisions.map((x) => (
                  <li key={x.decided_at + x.decision}><time className="faint num">{new Date(x.decided_at).toLocaleString("pt-BR")}</time><span className="dot" aria-hidden="true" />
                    <span>{DECISION_LABEL[x.decision] ?? x.decision}{x.comment ? ` — “${x.comment}”` : ""}</span></li>
                ))}
                {d.job && <li data-kind={d.job.status}><time className="faint">execução</time><span className="dot" aria-hidden="true" />
                  <span>{d.job.status} · {d.job.attempts} tentativa(s){d.job.provider_ref ? ` · ref ${d.job.provider_ref}` : ""}{d.job.error_kind ? ` — ${d.job.error_kind}: ${d.job.last_error}` : ""}</span></li>}
              </ol>
            </section>
          )}

        </div>
      )}
      {d && (canDecide ? (pending || revocable) : pending) && <div className="detail-foot">
          {canDecide && pending && (
            <div className="decision-bar">
              <button type="button" className="btn primary" disabled={!desk.online} onClick={() => setConfirming("approve")}><Check />Aprovar</button>
              <button type="button" className="btn" disabled={!desk.online} onClick={() => setConfirming("request-changes")}><MessageSquareWarning />Pedir mudanças</button>
              <button type="button" className="btn danger" disabled={!desk.online} onClick={() => setConfirming("reject")}><X />Rejeitar</button>
              {!desk.online && <span className="faint">Sem conexão — decisões bloqueadas.</span>}
            </div>
          )}
          {canDecide && revocable && (
            <div className="decision-bar"><button type="button" className="btn danger" disabled={!desk.online} onClick={() => setConfirming("revoke")}><Undo2 />Revogar antes da execução</button></div>
          )}
          {!canDecide && pending && <p className="faint" style={{ margin: 0 }}>Somente quem tem permissão de decisão (proprietário com MFA) decide propostas.</p>}
      </div>}
      {confirming && d && (
        <ConfirmDialog
          title={confirming === "approve" ? "Aprovar esta proposta?" : confirming === "reject" ? "Rejeitar esta proposta?" : confirming === "revoke" ? "Revogar a autorização?" : "Pedir mudanças ao agente?"}
          danger={confirming === "reject" || confirming === "revoke"}
          needReason={confirming !== "approve"}
          confirmLabel={confirming === "approve" ? "Confirmar aprovação" : confirming === "reject" ? "Confirmar rejeição" : confirming === "revoke" ? "Confirmar revogação" : "Enviar pedido"}
          body={confirming === "approve"
            ? <>Você aprova exatamente o conteúdo <code>{shortHash(d.payload_sha256)}</code> (versão {d.subject_version}){d.monetary_scope_minor > 0 && <>, com reserva de <strong>{formatMinor(d.monetary_scope_minor, d.currency)}</strong></>}. A execução só começa depois que o servidor confirmar.</>
            : confirming === "revoke" ? <>A execução na fila será cancelada e a reserva liberada.</>
              : <>O motivo fica registrado na auditoria e é mostrado ao agente.</>}
          onCancel={() => setConfirming(null)}
          onConfirm={(reason) => act(confirming, reason)} />
      )}
    </aside>
  );
}
