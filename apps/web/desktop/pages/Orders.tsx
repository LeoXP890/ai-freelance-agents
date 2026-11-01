"use client";
// UX-07 Pedidos & logística (UXD-013): visão operacional única para Logan (envio) e Eva (atendimento/devoluções).
// Tabela com filtros por etapa + painel de detalhe com linha do tempo real, itens com custo, envio, documento fiscal,
// devoluções e as ações do backend (compra no fornecedor gera proposta; cancelar/devolver pedem motivo e confirmação).
// Dados pessoais e chave fiscal só aparecem sob demanda, com acesso auditado.
import { Ban, Eye, FileText, PackageCheck, PackagePlus, RotateCcw, Search, ShoppingBag, Truck, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type Listing, type OrderDetail, type OrderSummary } from "@/lib/api";
import { since } from "@/lib/agentIdentity";
import { FISCAL_STATUS, formatMinor, nextOrderAction, ORDER_STATUS, PAYMENT_STATUS } from "@/lib/format";
import { useDesk } from "../context";
import { DataTable, Skeleton } from "../table";
import { ConfirmDialog, Empty } from "../ui";
import { channelName } from "./Finance";

type Tab = "ALL" | "OPEN" | "SHIPPING" | "SUPPORT" | "INCIDENT";
const OPEN = ["NEW", "PAYMENT_CONFIRMED", "FISCAL_PENDING", "READY_TO_FULFILL", "SUPPLIER_ACCEPTED"];
const TABS: [Tab, string, (o: OrderSummary) => boolean][] = [
  ["ALL", "Todos", () => true],
  ["OPEN", "A preparar", (o) => OPEN.includes(o.status)],
  ["SHIPPING", "Em trânsito", (o) => o.status === "SHIPPED"],
  ["SUPPORT", "Atendimento & devoluções", (o) => ["DELIVERED", "RETURN_REQUESTED", "REFUNDED"].includes(o.status)],
  ["INCIDENT", "Incidentes", (o) => o.status === "INCIDENT" || o.fiscal_status === "FISCAL_HOLD"],
];
const TONE: Record<string, string> = {
  NEW: "info", PAYMENT_CONFIRMED: "info", FISCAL_PENDING: "warn", READY_TO_FULFILL: "warn", SUPPLIER_ACCEPTED: "info",
  SHIPPED: "info", DELIVERED: "ok", CANCELLED: "", RETURN_REQUESTED: "warn", REFUNDED: "", INCIDENT: "crit",
};
const RETURN_REASON: Record<string, string> = { ARREPENDIMENTO: "Arrependimento (CDC, 7 dias)", DEFEITO: "Defeito", NAO_RECEBIDO: "Não recebido", DIVERGENTE: "Produto divergente", OUTRO: "Outro" };
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");
const orderChip = (s: string) => <span className={`dk-chip ${TONE[s] ?? ""}`}>{ORDER_STATUS[s] ?? s}</span>;

export default function Orders() {
  const desk = useDesk();
  const [tab, setTab] = useState<Tab>("ALL");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<OrderSummary[] | null>(null);
  const [listings, setListings] = useState<Listing[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sim = desk.can("sim:control") && desk.office.simulated;

  const load = useCallback(() => {
    api.orders(desk.token).then((r) => { setItems(r.items); setError(null); }).catch((e) => setError(msg(e)));
    if (sim) api.listings(desk.token).then((r) => setListings(r.items.filter((l) => l.status === "PUBLISHED"))).catch(() => undefined);
  }, [desk.token, sim]);
  useEffect(() => { if (desk.me) load(); }, [load, desk.refresh, desk.me]);

  const counts = useMemo(() => Object.fromEntries(TABS.map(([k, , f]) => [k, (items ?? []).filter(f).length])), [items]);
  const shown = useMemo(() => {
    const f = TABS.find((t) => t[0] === tab)![2];
    const t = q.trim().toLowerCase();
    return (items ?? []).filter(f).filter((o) => !t || `${o.external_id} ${o.channel} ${o.uf ?? ""}`.toLowerCase().includes(t));
  }, [items, tab, q]);

  if (desk.me && !desk.can("orders:read")) return <div className="page"><Empty icon={<ShoppingBag />} title="Seu papel não acessa pedidos" /></div>;
  return (
    <div className="page split-page">
      <div className="page-head">
        <div><h1>Pedidos & logística</h1><p>Pedidos de todos os canais, compra no fornecedor, envio, entrega, atendimento e devoluções.</p></div>
        <div className="actions">
          {sim && listings.length > 0 && desk.can("orders:manage") && (
            <button type="button" className="btn" disabled={!desk.online} onClick={async () => {
              try { const o = await api.simOrder(desk.token, listings[0]!.id); desk.notify("Pedido simulado criado."); load(); setSelected(o.id); }
              catch (e) { desk.notify(msg(e), "crit"); }
            }} title={`Usa o anúncio publicado: ${listings[0]!.title ?? ""}`}><PackagePlus />Simular pedido de cliente</button>
          )}
        </div>
      </div>
      <div className="filters">
        <div className="dk-seg" role="group" aria-label="Etapa">
          {TABS.map(([k, l]) => <button key={k} type="button" aria-pressed={tab === k} onClick={() => setTab(k)}>{l}{items ? ` · ${counts[k]}` : ""}</button>)}
        </div>
        <label className="search-field"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" placeholder="Buscar pedido, canal ou UF" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar pedidos" /></label>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      <div className={`split ${selected ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Lista de pedidos">
          {items === null ? <Skeleton /> : shown.length === 0 ? (
            <Empty icon={<ShoppingBag />} title={items.length ? "Nenhum pedido nesta etapa" : "Nenhum pedido ainda"}>Pedidos chegam pelos webhooks dos canais{sim ? " ou pelo botão de simulação" : ""}.</Empty>
          ) : (
            <DataTable id="orders" label="Pedidos" rows={shown} rowKey={(o) => o.id} selected={selected} onSelect={(o) => setSelected(o.id)}
              initialSort={{ key: "when", dir: -1 }} columns={[
                { key: "order", label: "Pedido", sort: (o) => o.external_id, render: (o) => <>
                  <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setSelected(o.id); }} aria-label={`Abrir pedido ${o.external_id}`}>#{o.external_id}</button>
                  <div className="faint">{o.uf ?? "—"}{o.simulated ? " · simulado" : ""}</div></> },
                { key: "channel", label: "Canal", sort: (o) => channelName(o.channel), render: (o) => channelName(o.channel) },
                { key: "status", label: "Situação", sort: (o) => ORDER_STATUS[o.status] ?? o.status, render: (o) => <>{orderChip(o.status)}{o.incident_reason && <div className="faint" style={{ color: "var(--critical)" }}>{o.incident_reason}</div>}</> },
                { key: "total", label: "Total", align: "right", sort: (o) => o.total_minor, render: (o) => formatMinor(o.total_minor, o.currency) },
                { key: "payment", label: "Pagamento", optional: true, sort: (o) => o.payment_status, render: (o) => <span className="faint">{PAYMENT_STATUS[o.payment_status] ?? o.payment_status}</span> },
                { key: "fiscal", label: "Fiscal", optional: true, sort: (o) => o.fiscal_status, render: (o) => o.fiscal_status === "FISCAL_HOLD" ? <span className="dk-chip warn">Nota retida</span> : <span className="faint">{FISCAL_STATUS[o.fiscal_status] ?? o.fiscal_status}</span> },
                { key: "uf", label: "UF", optional: true, hidden: true, sort: (o) => o.uf ?? "", render: (o) => o.uf ?? "—" },
                { key: "when", label: "Quando", sort: (o) => o.placed_at, render: (o) => <span className="faint">{since(o.placed_at)}</span> },
              ]} />
          )}
        </section>
        {selected && <OrderPane id={selected} onClose={() => setSelected(null)} onChanged={load} />}
      </div>
    </div>
  );
}

function OrderPane({ id, onClose, onChanged }: { id: string; onClose(): void; onChanged(): void }) {
  const desk = useDesk();
  const [o, setO] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [customer, setCustomer] = useState<Record<string, string | null> | null>(null);
  const [fiscal, setFiscal] = useState<{ id: string; access_key: string | null }[] | null>(null);
  const [confirm, setConfirm] = useState<"cancel" | "return" | null>(null);
  const [reason, setReason] = useState("ARREPENDIMENTO");
  const load = useCallback(() => {
    api.order(desk.token, id).then((r) => { setO(r); setError(null); }).catch((e) => setError(msg(e)));
  }, [desk.token, id]);
  useEffect(() => { setO(null); setCustomer(null); setFiscal(null); load(); }, [load]);
  useEffect(() => { if (o) load(); }, [desk.refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && !confirm) onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose, confirm]);

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true); setError(null);
    try { await fn(); desk.notify(done); load(); onChanged(); } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  };
  const f = o?.fulfillments.find((x) => x.status !== "CANCELLED" && x.status !== "FAILED") ?? null;
  const next = o ? nextOrderAction(o.status, f?.status ?? null) : null;
  const manage = desk.can("orders:manage");
  const simOk = desk.can("sim:control") && desk.office.simulated;
  const cost = o?.items.reduce((s, i) => s + (i.unit_cost_minor ?? 0) * i.qty, 0) ?? 0;
  const costKnown = o?.items.every((i) => i.unit_cost_minor !== null);

  return (
    <aside className="dk-card detail-pane" aria-label="Detalhe do pedido">
      <div className="detail-head">
        <div style={{ minWidth: 0 }}>
          <div className="faint">{o ? `${channelName(o.channel)} · ${new Date(o.placed_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}` : "Pedido"}{o?.simulated ? " · simulado" : ""}</div>
          <h2 className="detail-title">{o ? `Pedido #${o.external_id}` : "Carregando…"}</h2>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar pedido"><X size={18} /></button>
      </div>
      {error && <p role="alert" className="dk-error" style={{ padding: "8px 18px 0" }}>{error}</p>}
      {o && (
        <div className="detail-body">
          <div className="chip-row">{orderChip(o.status)}<span className="dk-chip">{PAYMENT_STATUS[o.payment_status] ?? o.payment_status}</span><span className={`dk-chip ${o.fiscal_status === "FISCAL_HOLD" ? "warn" : ""}`}>{FISCAL_STATUS[o.fiscal_status] ?? o.fiscal_status}</span></div>
          {o.incident_reason && <div className="callout crit" role="note">{o.incident_reason}</div>}
          <div className="facts">
            <div><span className="faint">Total pago</span><strong className="num">{formatMinor(o.total_minor, o.currency)}</strong></div>
            <div><span className="faint">Custo dos itens</span><strong className="num">{costKnown ? formatMinor(cost) : "desconhecido"}</strong><span className="faint">fornecedor, sem frete/tarifas</span></div>
          </div>
          <section aria-label="Itens">
            <h3 className="sec-title">Itens</h3>
            <table className="dk-table"><tbody>{o.items.map((i) => (
              <tr key={i.sku} className="static"><td>{i.qty}× <strong>{i.sku}</strong></td><td className="r num">{formatMinor(i.unit_price_minor)}</td>
                <td className="r num faint">{i.unit_cost_minor !== null ? `custo ${formatMinor(i.unit_cost_minor)}` : "custo —"}</td></tr>))}</tbody></table>
          </section>
          <section aria-label="Envio">
            <h3 className="sec-title">Envio</h3>
            {f ? <div style={{ fontSize: 13 }}><strong>{f.status}</strong>{f.carrier ? ` · ${f.carrier}` : ""}{f.tracking_code ? <> · rastreio <code>{f.tracking_code}</code></> : ""}
              <div className="faint">prometido até {new Date(f.promised_by).toLocaleDateString("pt-BR")} · custo {formatMinor(f.cost_minor)}</div></div>
              : <p className="muted" style={{ margin: 0 }}>Ainda sem compra no fornecedor.</p>}
          </section>
          {o.fiscal_documents.length > 0 && (
            <section aria-label="Documento fiscal">
              <h3 className="sec-title">Documento fiscal</h3>
              {o.fiscal_documents.map((d) => (
                <div key={d.id} style={{ fontSize: 13 }}><FileText size={14} style={{ verticalAlign: -2 }} /> {d.kind} <strong>{d.status}</strong> · {d.environment}
                  {d.access_key && <div className="faint">chave <code>{fiscal?.find((x) => x.id === d.id)?.access_key ?? d.access_key}</code></div>}
                  {d.reason && <div className="faint">{d.reason}</div>}</div>
              ))}
              {desk.can("fiscal:read") && !fiscal && <button type="button" className="dk-link" onClick={() => run(async () => setFiscal((await api.fiscalDocuments(desk.token, o.id)).items), "Acesso ao documento fiscal registrado na auditoria.")}><Eye size={14} />Ver chave completa (acesso registrado)</button>}
            </section>
          )}
          {o.returns.length > 0 && (
            <section aria-label="Devoluções">
              <h3 className="sec-title">Devoluções</h3>
              {o.returns.map((r) => <div key={r.id} style={{ fontSize: 13 }}>{RETURN_REASON[r.reason] ?? r.reason}: <strong>{r.status}</strong>{r.withdrawal_right ? " · direito de arrependimento (CDC)" : ""} · {formatMinor(r.refund_minor)}</div>)}
            </section>
          )}
          <section aria-label="Cliente">
            <h3 className="sec-title">Cliente</h3>
            <div style={{ fontSize: 13 }}>{customer ? Object.entries(customer).filter(([, v]) => v).map(([k, v]) => <div key={k}><span className="faint">{k}:</span> {v}</div>)
              : <>{o.customer.name ?? "—"} <span className="faint">({o.customer.uf ?? o.uf ?? "—"})</span></>}</div>
            {desk.can("orders:pii") && !customer && <button type="button" className="dk-link" onClick={() => run(async () => setCustomer(await api.orderCustomer(desk.token, o.id)), "Acesso aos dados do cliente registrado na auditoria.")}><Eye size={14} />Ver dados do cliente (acesso registrado)</button>}
          </section>
          <section aria-label="Linha do tempo do pedido">
            <h3 className="sec-title">Linha do tempo</h3>
            <ol className="timeline">{o.events.map((e, i) => (
              <li key={i} data-kind={e.to === "DELIVERED" ? "COMPLETED" : e.to === "INCIDENT" ? "FAILED" : undefined}>
                <time className="faint num">{new Date(e.at).toLocaleTimeString("pt-BR")}</time><span className="dot" aria-hidden="true" />
                <span>{ORDER_STATUS[e.to] ?? e.to} <span className="faint">· {e.source}</span></span></li>))}</ol>
          </section>
        </div>
      )}
      {o && manage && (
        <div className="detail-foot"><div className="decision-bar">
          {next === "fulfill" && <button type="button" className="btn primary" disabled={busy || !desk.online} onClick={() => run(() => api.fulfillOrder(desk.token, o.id), "Compra no fornecedor proposta — aguarda aprovação.")}><PackageCheck />Solicitar compra no fornecedor</button>}
          {next === "ship" && simOk && f && <button type="button" className="btn" disabled={busy || !desk.online} onClick={() => run(() => api.simShipping(desk.token, f.id, "ship"), "Envio simulado.")}><Truck />Simular envio</button>}
          {next === "deliver" && simOk && f && <button type="button" className="btn" disabled={busy || !desk.online} onClick={() => run(() => api.simShipping(desk.token, f.id, "deliver"), "Entrega simulada.")}><PackageCheck />Simular entrega</button>}
          {["SHIPPED", "DELIVERED"].includes(o.status) && <button type="button" className="btn" disabled={busy || !desk.online} onClick={() => setConfirm("return")}><RotateCcw />Abrir devolução</button>}
          {["NEW", "PAYMENT_CONFIRMED", "FISCAL_PENDING", "READY_TO_FULFILL", "INCIDENT"].includes(o.status) && <button type="button" className="btn danger" disabled={busy || !desk.online} onClick={() => setConfirm("cancel")}><Ban />Cancelar pedido</button>}
          {!desk.online && <span className="faint">Sem conexão — ações bloqueadas.</span>}
        </div></div>
      )}
      {confirm === "cancel" && o && (
        <ConfirmDialog title={`Cancelar o pedido #${o.external_id}?`} danger needReason confirmLabel="Cancelar pedido"
          body="O cancelamento é registrado no pedido e na auditoria. Reembolsos seguem pelo fluxo de aprovação." onCancel={() => setConfirm(null)}
          onConfirm={async (r) => { await api.cancelOrder(desk.token, o.id, r); setConfirm(null); desk.notify("Pedido cancelado no servidor.", "warn"); load(); onChanged(); }} />
      )}
      {confirm === "return" && o && (
        <ConfirmDialog title={`Abrir devolução do pedido #${o.external_id}?`} needReason confirmLabel="Abrir devolução"
          body={<label style={{ display: "grid", gap: 6 }}><span>Motivo da devolução</span>
            <select className="field" value={reason} onChange={(e) => setReason(e.target.value)}>{Object.entries(RETURN_REASON).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <span className="faint">O reembolso vira proposta e só é pago depois de aprovado.</span></label>}
          onCancel={() => setConfirm(null)}
          onConfirm={async (desc) => { await api.openReturn(desk.token, o.id, reason, desc); setConfirm(null); desk.notify("Devolução aberta — reembolso aguarda aprovação."); load(); onChanged(); }} />
      )}
    </aside>
  );
}
