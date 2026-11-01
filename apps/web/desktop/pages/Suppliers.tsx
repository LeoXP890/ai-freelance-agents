"use client";
// UX-06 Fornecedores (UXD-012): visão comparativa (risco, checklist SUP-01, prazo, amostra, ofertas atualizadas ×
// obsoletas) + dossiê com documentos, ofertas e transições. Risco e prontidão são calculados no backend; aprovar
// exige permissão e MFA, e o servidor recusa se o checklist estiver incompleto.
import { AlertTriangle, Boxes, CheckCircle2, CircleSlash, FilePlus2, Search, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type SupplierDetail, type SupplierSummary } from "@/lib/api";
import { formatMinor, hoursSince, SUPPLIER_STATUS } from "@/lib/format";
import { Meter } from "../charts";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { ConfirmDialog, Empty } from "../ui";

const STONE: Record<string, string> = { PROSPECT: "", EVALUATING: "info", APPROVED: "ok", SUSPENDED: "warn", REJECTED: "crit" };
const RISK: Record<string, string> = { BAIXO: "ok", MEDIO: "warn", MÉDIO: "warn", ALTO: "crit" };
const SAMPLE: Record<string, string> = { NOT_REQUESTED: "Não solicitada", REQUESTED: "Solicitada", RECEIVED: "Recebida", APPROVED: "Aprovada", REJECTED: "Reprovada" };
const DOC: Record<string, string> = { CNPJ_CARD: "Cartão CNPJ", FOREIGN_REGISTRATION: "Registro estrangeiro", CONTRACT: "Contrato", INVOICE_SAMPLE: "Nota de amostra",
  CERTIFICATE: "Certificado", PRODUCT_PHOTO: "Foto do produto", SLA_TERMS: "Termos de SLA", OTHER: "Outro" };
const STALE_H = 24;
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function Suppliers() {
  const desk = useDesk();
  const [items, setItems] = useState<SupplierSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.suppliers(desk.token).then((r) => { setItems(r.items); setError(null); }).catch((e) => setError(msg(e)));
  }, [desk.token]);
  useEffect(() => { if (desk.me) load(); }, [load, desk.refresh, desk.me]);
  const shown = useMemo(() => (items ?? []).filter((s) => !status || s.status === status)
    .filter((s) => !q || `${s.name} ${s.country}`.toLowerCase().includes(q.toLowerCase())), [items, q, status]);

  return (
    <div className="page split-page">
      <div className="page-head"><div><h1>Fornecedores</h1><p>Comparativo de fornecedores com risco, evidências (checklist SUP-01) e ofertas — verificadas e desatualizadas separadas.</p></div></div>
      <div className="filters">
        <label className="search-field"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" placeholder="Buscar fornecedor ou país" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar fornecedores" /></label>
        <select className="field" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Situação">
          <option value="">Qualquer situação</option>{Object.entries(SUPPLIER_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <span className="faint" style={{ marginLeft: "auto" }}>{items ? `${items.length} fornecedores · ${items.filter((s) => s.status === "APPROVED").length} aprovados` : ""}</span>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      <div className={`split ${selected ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Comparativo de fornecedores">
          {items === null ? <Skeleton /> : shown.length === 0 ? <Empty icon={<Boxes />} title="Nenhum fornecedor com esse filtro" /> : (
            <table className="dk-table">
              <thead><tr><th>Fornecedor</th><th>Situação</th><th>Risco</th><th>Evidências</th><th className="r opt">Despacho</th><th className="opt">Amostra</th></tr></thead>
              <tbody>{shown.map((s) => {
                const ok = s.assessment.checklist.filter((i) => i.ok).length, n = s.assessment.checklist.length;
                return (
                  <tr key={s.id} aria-selected={selected === s.id} onClick={() => setSelected(s.id)}>
                    <td><button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setSelected(s.id); }} aria-label={`Abrir dossiê de ${s.name}`}>{s.name}</button>
                      <div className="faint">{s.origin === "NATIONAL" ? "Nacional" : "Internacional"} · {s.country}{s.simulated ? " · simulado" : ""}</div></td>
                    <td><span className={`dk-chip ${STONE[s.status] ?? ""}`}>{SUPPLIER_STATUS[s.status] ?? s.status}</span></td>
                    <td><span className={`dk-chip ${RISK[s.assessment.level] ?? ""}`}>{s.assessment.level} · {s.assessment.score}</span></td>
                    <td style={{ minWidth: 120 }}><span className="num">{ok}/{n}</span><Meter used={ok} total={n} label={`Checklist de ${s.name}`} progress />
                      {s.assessment.stale_offers > 0 && <span className="dk-chip warn"><AlertTriangle aria-hidden="true" />{s.assessment.stale_offers} oferta(s) obsoleta(s)</span>}</td>
                    <td className="r num opt">{s.sla_dispatch_days !== null ? `${s.sla_dispatch_days} d` : "—"}</td>
                    <td className="opt faint">{SAMPLE[s.sample_status] ?? s.sample_status}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          )}
        </section>
        {selected && <Dossier id={selected} onClose={() => setSelected(null)} onChanged={load} />}
      </div>
    </div>
  );
}

function Dossier({ id, onClose, onChanged }: { id: string; onClose(): void; onChanged(): void }) {
  const desk = useDesk();
  const [d, setD] = useState<SupplierDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [move, setMove] = useState<{ to: string; label: string; danger?: boolean } | null>(null);
  const [docType, setDocType] = useState("CONTRACT");
  const [docRef, setDocRef] = useState("");
  const load = useCallback(() => { api.supplier(desk.token, id).then((r) => { setD(r); setError(null); }).catch((e) => setError(msg(e))); }, [desk.token, id]);
  useEffect(() => { setD(null); load(); }, [load]);
  const write = desk.can("catalog:write");
  const approve = desk.can("suppliers:approve");
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); desk.notify(ok); load(); onChanged(); } catch (e) { setError(msg(e)); }
  };
  const a = d?.assessment;
  return (
    <aside className="dk-card detail-pane" aria-label="Dossiê do fornecedor">
      <div className="detail-head">
        <div style={{ minWidth: 0 }}><div className="faint">{d ? `${d.origin === "NATIONAL" ? "Nacional" : "Internacional"} · ${d.country} · v${d.version}${d.simulated ? " · simulado" : ""}` : "Fornecedor"}</div>
          <h2 className="detail-title">{d?.name ?? "Carregando…"}</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar dossiê"><X size={18} /></button>
      </div>
      {error && <p role="alert" className="dk-error" style={{ padding: "8px 18px 0" }}>{error}</p>}
      {d && a && (
        <div className="detail-body">
          <div className="chip-row"><span className={`dk-chip ${STONE[d.status] ?? ""}`}>{SUPPLIER_STATUS[d.status] ?? d.status}</span>
            <span className={`dk-chip ${RISK[a.level] ?? ""}`}>Risco {a.level} · {a.score}/100</span>
            {a.ready_for_approval ? <span className="dk-chip ok"><CheckCircle2 aria-hidden="true" />Pronto para aprovação</span> : <span className="dk-chip warn"><AlertTriangle aria-hidden="true" />Checklist incompleto</span>}</div>
          <section aria-label="Checklist de evidências">
            <h3 className="sec-title">Checklist SUP-01</h3>
            <ul className="checklist-ul">{a.checklist.map((i) => (
              <li key={i.key} className={i.ok ? "is-ok" : "is-miss"}>{i.ok ? <CheckCircle2 size={16} aria-label="cumprido" /> : <CircleSlash size={16} aria-label="pendente" />}
                <span><strong>{i.label}</strong><span className="faint" style={{ display: "block" }}>{i.detail}</span></span></li>))}</ul>
          </section>
          <section aria-label="Ofertas">
            <h3 className="sec-title">Ofertas ({d.offers.length})</h3>
            {d.offers.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma oferta.</p> : (
              <table className="dk-table"><thead><tr><th className="r">Custo</th><th className="r">Estoque</th><th className="r">Prazo</th><th>Observado</th></tr></thead>
                <tbody>{d.offers.map((o) => { const h = hoursSince(o.stock_observed_at); return (
                  <tr key={o.id} className="static"><td className="r num">{formatMinor(o.cost_minor, o.currency)}</td><td className="r num">{o.stock_qty}</td><td className="r num">{o.lead_time_days} d</td>
                    <td>{h > STALE_H ? <span className="dk-chip warn"><AlertTriangle aria-hidden="true" />obsoleto · {h} h</span> : <span className="dk-chip ok">atualizado · {h} h</span>}</td></tr>); })}</tbody></table>
            )}
          </section>
          <section aria-label="Documentos">
            <h3 className="sec-title">Documentos ({d.documents.length})</h3>
            <div className="rows">{d.documents.map((doc) => (
              <div key={doc.id} className="row-item"><span className="grow"><span className="t">{DOC[doc.doc_type] ?? doc.doc_type}</span><span className="meta ellipsis" style={{ display: "block" }}>{doc.reference}</span></span>
                {doc.verified ? <span className="dk-chip ok"><ShieldCheck aria-hidden="true" />verificado</span>
                  : approve ? <button type="button" className="btn" disabled={!desk.online} onClick={() => act(() => api.verifySupplierDocument(desk.token, d.id, doc.id), "Documento verificado (auditado).")}>Marcar verificado</button>
                    : <span className="dk-chip warn">não verificado</span>}</div>))}
              {d.documents.length === 0 && <p className="muted" style={{ margin: 0 }}>Nenhum documento.</p>}</div>
            {write && (
              <form className="row-actions" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); void act(async () => { await api.supplierDocument(desk.token, d.id, docType, docRef); setDocRef(""); }, "Documento registrado; aguarda verificação."); }}>
                <select className="field" value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Tipo de documento">{Object.entries(DOC).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                <input className="field" style={{ flex: 1, minWidth: 160 }} required minLength={3} value={docRef} onChange={(e) => setDocRef(e.target.value)} placeholder="Onde o documento está guardado" aria-label="Referência do documento" />
                <button type="submit" className="btn" disabled={!desk.online}><FilePlus2 />Registrar</button>
              </form>
            )}
          </section>
          <section aria-label="Amostra">
            <h3 className="sec-title">Amostra</h3>
            <div className="row-actions"><span className="dk-chip">{SAMPLE[d.sample_status] ?? d.sample_status}</span>
              {write && <select className="field" aria-label="Atualizar amostra" value="" onChange={(e) => e.target.value && act(() => api.supplierSample(desk.token, d.id, e.target.value, d.version), "Situação da amostra atualizada.")}>
                <option value="">Atualizar…</option>{Object.entries(SAMPLE).filter(([k]) => k !== d.sample_status && (k !== "APPROVED" || approve)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>}</div>
          </section>
          {d.note && <p className="faint" style={{ margin: 0 }}>{d.note}</p>}
        </div>
      )}
      {d && write && (
        <div className="detail-foot"><div className="decision-bar">
          {["PROSPECT", "SUSPENDED", "REJECTED"].includes(d.status) && <button type="button" className="btn" disabled={!desk.online} onClick={() => setMove({ to: "EVALUATING", label: "Iniciar avaliação" })}>Iniciar avaliação</button>}
          {d.status === "EVALUATING" && approve && <button type="button" className="btn primary" disabled={!desk.online || !a?.ready_for_approval} title={a?.ready_for_approval ? "" : "Checklist incompleto"} onClick={() => setMove({ to: "APPROVED", label: "Aprovar fornecedor" })}><ShieldCheck />Aprovar fornecedor</button>}
          {d.status === "APPROVED" && approve && <button type="button" className="btn danger" disabled={!desk.online} onClick={() => setMove({ to: "SUSPENDED", label: "Suspender", danger: true })}>Suspender</button>}
          {["PROSPECT", "EVALUATING", "SUSPENDED"].includes(d.status) && <button type="button" className="btn danger" disabled={!desk.online} onClick={() => setMove({ to: "REJECTED", label: "Rejeitar", danger: true })}>Rejeitar</button>}
        </div></div>
      )}
      {move && d && <ConfirmDialog title={`${move.label}: ${d.name}?`} danger={move.danger} needReason confirmLabel={move.label}
        body={move.to === "APPROVED" ? "Exige MFA e checklist completo; o servidor confere de novo antes de aprovar." : "O motivo fica no histórico do fornecedor e na auditoria."}
        onCancel={() => setMove(null)}
        onConfirm={async (r) => { await api.supplierTransition(desk.token, d.id, move.to, d.version, r); setMove(null); desk.notify(`${move.label}: registrado no servidor.`); load(); onChanged(); }} />}
    </aside>
  );
}
