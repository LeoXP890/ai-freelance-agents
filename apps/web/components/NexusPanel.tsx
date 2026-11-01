"use client";
// Painel da Nexus (fornecedores): dossiê, checklist SUP-01 e risco calculados pelo backend (§24.1).
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type SupplierDetail, type SupplierSummary } from "@/lib/api";
import { formatMinor, hoursSince, SUPPLIER_STATUS } from "@/lib/format";

export default function NexusPanel({ token, canWrite, canApprove, refreshSignal }: {
  token: string; canWrite: boolean; canApprove: boolean; refreshSignal: number;
}) {
  const [items, setItems] = useState<SupplierSummary[]>([]);
  const [detail, setDetail] = useState<SupplierDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const detailId = detail?.id ?? null;
  const load = useCallback(async () => {
    try {
      setItems((await api.suppliers(token)).items);
      if (detailId) setDetail(await api.supplier(token, detailId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha de conexão com a API");
    }
  }, [token, detailId]);

  useEffect(() => { void load(); }, [load, refreshSignal]);

  async function move(to: string, reason: string) {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      setDetail(await api.supplierTransition(token, detail.id, to, detail.version, reason));
      void load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha de conexão com a API");
    } finally {
      setBusy(false);
    }
  }

  if (detail) {
    const a = detail.assessment;
    return (
      <div className="dept">
        <button type="button" className="link" onClick={() => setDetail(null)}>← Fornecedores</button>
        <h3>{detail.name} {detail.simulated && <span className="sim">Simulado</span>}</h3>
        <div className="muted small">{detail.origin === "NATIONAL" ? "Nacional" : "Internacional"} · {detail.country}
          · {SUPPLIER_STATUS[detail.status] ?? detail.status} · v{detail.version}</div>
        <div className={`risk risk-${a.level}`}>Risco {a.level} · {a.score}/100</div>
        <ul className="checklist" aria-label="Checklist de evidências (SUP-01)">
          {a.checklist.map((i) => (
            <li key={i.key} className={i.ok ? "ok" : "missing"}>
              <span aria-hidden="true">{i.ok ? "✓" : "✗"}</span> {i.label}
              <span className="muted small"> — {i.detail}</span>
            </li>
          ))}
        </ul>
        <h4>Ofertas</h4>
        {detail.offers.length === 0 ? <p className="muted small">Nenhuma oferta.</p> : (
          <ul className="list">
            {detail.offers.map((o) => {
              const h = hoursSince(o.stock_observed_at);
              return (
                <li key={o.id}>{formatMinor(o.cost_minor)} · estoque {o.stock_qty} · prazo {o.lead_time_days} d
                  <span className={h > 24 ? "warn small" : "muted small"}> · visto há {h} h{h > 24 ? " (obsoleto)" : ""}</span>
                </li>
              );
            })}
          </ul>
        )}
        <h4>Documentos</h4>
        <ul className="list">
          {detail.documents.map((d) => (
            <li key={d.id}>{d.doc_type} {d.verified ? <span className="good small">verificado</span>
              : <span className="warn small">não verificado</span>}</li>
          ))}
          {detail.documents.length === 0 && <li className="muted small">Nenhum documento.</li>}
        </ul>
        {error && <p className="err" role="alert">{error}</p>}
        <div className="row actions">
          {canWrite && detail.status === "PROSPECT" && (
            <button type="button" disabled={busy} onClick={() => move("EVALUATING", "iniciar avaliação pelo painel")}>
              Iniciar avaliação</button>
          )}
          {canApprove && detail.status === "EVALUATING" && (
            <button type="button" className="primary" disabled={busy || !a.ready_for_approval}
              title={a.ready_for_approval ? "" : "Checklist incompleto"}
              onClick={() => move("APPROVED", "checklist completo; aprovado pelo painel da Nexus")}>Aprovar fornecedor</button>
          )}
          {canApprove && detail.status === "APPROVED" && (
            <button type="button" className="danger" disabled={busy}
              onClick={() => move("SUSPENDED", "suspenso pelo painel")}>Suspender</button>
          )}
        </div>
        <p className="muted small">{detail.note}</p>
      </div>
    );
  }

  return (
    <div className="dept">
      <h3>Fornecedores</h3>
      {error && <p className="err" role="alert">{error}</p>}
      {items.length === 0 ? <p className="muted small">Nenhum fornecedor cadastrado.</p> : (
        <ul className="inbox">
          {items.map((s) => (
            <li key={s.id}>
              <button type="button" className="inbox-item" onClick={async () => setDetail(await api.supplier(token, s.id))}>
                <span className="inbox-title">{s.name}</span>
                <span className="inbox-meta">
                  <span>{SUPPLIER_STATUS[s.status] ?? s.status}</span>
                  <span className={`risk risk-${s.assessment.level}`}>Risco {s.assessment.level}</span>
                  <span className="muted">{s.assessment.checklist.filter((i) => i.ok).length}/{s.assessment.checklist.length}</span>
                  {s.simulated && <span className="sim">Simulado</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
