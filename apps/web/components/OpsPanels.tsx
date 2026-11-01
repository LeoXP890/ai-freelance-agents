"use client";
// Painéis de operação (§24.1 "pedidos ↔ operações", "financeiro ↔ painel"): Logan (pedidos/envio),
// Eva (devoluções/atendimento) e Finn (orçamento/conciliação/alertas). Todo estado vem do backend.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Alert, type Listing, type OrderDetail, type OrderSummary, type Reconciliation } from "@/lib/api";
import { formatMinor, nextOrderAction, ORDER_STATUS } from "@/lib/format";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

function OrderDetailView({ token, id, refreshSignal, onBack, mode, perms, onProposed }: {
  token: string; id: string; refreshSignal: number; onBack(): void; mode: "logan" | "eva"; perms: string[];
  onProposed(): void;
}) {
  const [o, setO] = useState<OrderDetail | null>(null);
  const [customer, setCustomer] = useState<Record<string, string | null> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("ARREPENDIMENTO");
  const [fiscal, setFiscal] = useState<{ id: string; access_key: string | null }[] | null>(null);
  const load = useCallback(async () => {
    try { setO(await api.order(token, id)); } catch (e) { setError(msg(e)); }
  }, [token, id]);
  useEffect(() => { void load(); }, [load, refreshSignal]);
  if (!o) return <p className="muted small">{error ?? "Carregando…"}</p>;

  const f = o.fulfillments.find((x) => x.status !== "CANCELLED" && x.status !== "FAILED") ?? null;
  const next = nextOrderAction(o.status, f?.status ?? null);
  const run = async (fn: () => Promise<unknown>, proposed = false) => {
    setBusy(true); setError(null);
    try { await fn(); await load(); if (proposed) onProposed(); } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  };
  const manage = perms.includes("orders:manage");
  const sim = perms.includes("sim:control");

  return (
    <div className="dept">
      <button type="button" className="link" onClick={onBack}>← Pedidos</button>
      <h3>Pedido {o.external_id} {o.simulated && <span className="sim">Simulado</span>}</h3>
      <div className="muted small">{o.channel} · {ORDER_STATUS[o.status] ?? o.status} · {formatMinor(o.total_minor)}
        · pagamento {o.payment_status} · fiscal {o.fiscal_status}</div>
      {o.incident_reason && <p className="err small">{o.incident_reason}</p>}
      <ul className="list">{o.items.map((i) => <li key={i.sku}>{i.qty}× {i.sku} · {formatMinor(i.unit_price_minor)}
        {i.unit_cost_minor !== null && <span className="muted small"> (custo {formatMinor(i.unit_cost_minor)})</span>}</li>)}</ul>
      {o.fiscal_documents.map((d) => (
        <div key={d.kind} className="job small">NF-e: <strong>{d.status}</strong> · {d.environment}
          {d.access_key && <div>Chave <code>{fiscal?.find((x) => x.id === d.id)?.access_key ?? d.access_key}</code></div>}
          {d.reason && <div className="muted">{d.reason}</div>}</div>
      ))}
      {o.fiscal_documents.length > 0 && perms.includes("fiscal:read") && !fiscal && (
        <button type="button" className="link" onClick={() => run(async () => setFiscal((await api.fiscalDocuments(token, o.id)).items))}>
          Ver documento fiscal completo (acesso registrado)
        </button>
      )}
      {f && <div className="small">Envio: {f.status}{f.tracking_code && <> · <code>{f.tracking_code}</code></>}
        · previsto até {new Date(f.promised_by).toLocaleDateString("pt-BR")}</div>}
      <ol className="timeline">{o.events.map((e, i) => <li key={i}><span className="muted small">
        {new Date(e.at).toLocaleTimeString("pt-BR")}</span> {ORDER_STATUS[e.to] ?? e.to}</li>)}</ol>

      {mode === "logan" && manage && (
        <div className="row actions">
          {next === "fulfill" && <button type="button" className="primary" disabled={busy}
            onClick={() => run(() => api.fulfillOrder(token, o.id), true)}>Solicitar compra no fornecedor</button>}
          {next === "ship" && sim && f && <button type="button" disabled={busy}
            onClick={() => run(() => api.simShipping(token, f.id, "ship"))}>Simular envio</button>}
          {next === "deliver" && sim && f && <button type="button" disabled={busy}
            onClick={() => run(() => api.simShipping(token, f.id, "deliver"))}>Simular entrega</button>}
          {["NEW", "PAYMENT_CONFIRMED", "FISCAL_PENDING", "READY_TO_FULFILL", "INCIDENT"].includes(o.status) && (
            <button type="button" className="danger" disabled={busy}
              onClick={() => run(() => api.cancelOrder(token, o.id, "cancelado pela operação"))}>Cancelar</button>)}
        </div>
      )}
      {mode === "eva" && (
        <>
          {o.returns.map((r) => <div key={r.id} className="small">Devolução {r.reason}: <strong>{r.status}</strong>
            {r.withdrawal_right && " · direito de arrependimento (CDC)"} · {formatMinor(r.refund_minor)}</div>)}
          {manage && ["SHIPPED", "DELIVERED"].includes(o.status) && (
            <div className="row actions">
              <select aria-label="Motivo da devolução" value={reason} onChange={(e) => setReason(e.target.value)}>
                {["ARREPENDIMENTO", "DEFEITO", "NAO_RECEBIDO", "DIVERGENTE", "OUTRO"].map((x) => <option key={x}>{x}</option>)}
              </select>
              <button type="button" disabled={busy}
                onClick={() => run(() => api.openReturn(token, o.id, reason, "aberto pelo painel da Eva"), true)}>
                Abrir devolução</button>
            </div>
          )}
          <div className="small">Cliente: {customer ? Object.entries(customer).filter(([, v]) => v)
            .map(([k, v]) => `${k}: ${v}`).join(" · ") : `${o.customer.name ?? "—"} (${o.customer.uf ?? "—"})`}</div>
          {perms.includes("orders:pii") && !customer && (
            <button type="button" onClick={() => run(async () => setCustomer(await api.orderCustomer(token, o.id)))}>
              Ver dados do cliente (acesso registrado)</button>)}
        </>
      )}
      {error && <p className="err" role="alert">{error}</p>}
    </div>
  );
}

export function OrdersPanel({ token, mode, perms, refreshSignal, onProposed }: {
  token: string; mode: "logan" | "eva"; perms: string[]; refreshSignal: number; onProposed(): void;
}) {
  const [items, setItems] = useState<OrderSummary[]>([]);
  const [listings, setListings] = useState<Listing[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setItems((await api.orders(token)).items);
      if (perms.includes("sim:control")) setListings((await api.listings(token)).items.filter((l) => l.status === "PUBLISHED"));
    } catch (e) { setError(msg(e)); }
  }, [token, perms]);
  useEffect(() => { void load(); }, [load, refreshSignal]);

  if (selected) return <OrderDetailView token={token} id={selected} refreshSignal={refreshSignal} mode={mode}
    perms={perms} onProposed={onProposed} onBack={() => setSelected(null)} />;

  const shown = mode === "eva" ? items.filter((o) => ["SHIPPED", "DELIVERED", "RETURN_REQUESTED", "REFUNDED"].includes(o.status))
    : items;
  return (
    <div className="dept">
      <h3>{mode === "eva" ? "Atendimento e devoluções" : "Pedidos e envios"}</h3>
      {mode === "logan" && perms.includes("sim:control") && listings.length > 0 && (
        <button type="button" onClick={async () => {
          try { const o = await api.simOrder(token, listings[0]!.id); setSelected(o.id); } catch (e) { setError(msg(e)); }
        }}>Simular pedido de cliente ({listings[0]!.title?.slice(0, 24)}…)</button>
      )}
      {shown.length === 0 ? <p className="muted small">Nenhum pedido.</p> : (
        <ul className="inbox">{shown.map((o) => (
          <li key={o.id}><button type="button" className="inbox-item" onClick={() => setSelected(o.id)}>
            <span className="inbox-title">{o.external_id} · {formatMinor(o.total_minor)}</span>
            <span className="inbox-meta"><span>{o.channel}</span><span>{ORDER_STATUS[o.status] ?? o.status}</span>
              {o.status === "INCIDENT" && <span className="err">incidente</span>}
              {o.fiscal_status === "FISCAL_HOLD" && <span className="warn">fiscal retido</span>}
              {o.simulated && <span className="sim">Simulado</span>}</span>
          </button></li>))}</ul>
      )}
      {error && <p className="err" role="alert">{error}</p>}
    </div>
  );
}

export function FinancePanel({ token, perms, refreshSignal }: { token: string; perms: string[]; refreshSignal: number }) {
  const [accounts, setAccounts] = useState<Array<Record<string, unknown>>>([]);
  const [rec, setRec] = useState<Reconciliation | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [f, r, a] = await Promise.all([api.finance(token), api.reconciliation(token), api.alerts(token)]);
      setAccounts(f.accounts); setRec(r); setAlerts(a.items);
    } catch (e) { setError(msg(e)); }
  }, [token]);
  useEffect(() => { void load(); }, [load, refreshSignal]);
  return (
    <div className="dept">
      <h3>Orçamento</h3>
      <ul className="list">{accounts.map((a) => (
        <li key={String(a.code)}>{String(a.name)}: disponível <strong>{formatMinor(Number(a.available_minor ?? 0))}</strong>
          <span className="muted small"> · gasto {formatMinor(Number(a.reconciled_spend_minor ?? 0))} · reservado {formatMinor(Number(a.outstanding_reservations_minor ?? 0))}</span></li>
      ))}</ul>
      <h3>Conciliação de repasses</h3>
      {rec && <dl className="kv">
        <dt>Conciliados</dt><dd>{rec.totals.matched}</dd>
        <dt>Divergentes</dt><dd className={rec.totals.divergent ? "err" : ""}>{rec.totals.divergent}
          {rec.totals.divergent > 0 && ` (${formatMinor(rec.totals.divergence_minor)})`}</dd>
        <dt>A receber</dt><dd>{formatMinor(rec.totals.receivable_open_minor)}</dd>
        <dt>Linhas órfãs</dt><dd>{rec.totals.orphans}</dd></dl>}
      {perms.includes("sim:control") && (
        <button type="button" onClick={async () => {
          try { await api.simSettlement(token, "WOOCOMMERCE"); await load(); } catch (e) { setError(msg(e)); }
        }}>Simular repasse do WooCommerce</button>
      )}
      {rec && <p className="muted small">{rec.note}</p>}
      <h3>Alertas ({alerts.length})</h3>
      <ul className="issues">{alerts.slice(0, 12).map((a, i) => (
        <li key={i} className={a.severity === "CRITICAL" ? "err" : "warn"}>{a.message}</li>))}</ul>
      {error && <p className="err" role="alert">{error}</p>}
    </div>
  );
}
