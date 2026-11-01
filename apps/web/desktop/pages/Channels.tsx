"use client";
// UX-07 Canais de venda (UXD-012): estado real de cada canal (Mock / Não conectado / Conectado / Problema +
// homologação pendente — nunca um "verde genérico"), anúncios com preço, margem e conformidade calculados no backend,
// e o assistente de preparar anúncio. Publicar ou pausar sempre vira PROPOSTA para a Central de Aprovações.
import { AlertTriangle, CheckCircle2, Megaphone, PauseCircle, Plus, Search, Send, ShieldAlert, Store, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type GateRow, type Listing, type Offer, type ProductSummary, type SupplierSummary } from "@/lib/api";
import { type Account, accountFor, CHANNEL_GATE, CHANNELS, channelHealth, gatePending } from "@/lib/channels";
import { formatBps, formatMinor, LISTING_STATUS } from "@/lib/format";
import { ELIG, type EligibilityRow, liveExplanation, SELLER_SHORT } from "@/lib/seller";
import { useDesk } from "../context";
import { openExternal } from "../native";
import { Skeleton } from "../table";
import { ConfirmDialog, Empty } from "../ui";
import { channelName } from "./Finance";

const LTONE: Record<string, string> = { DRAFT: "", PENDING_APPROVAL: "warn", PUBLISHED: "ok", PAUSED: "", ERROR: "crit", DELETED_EXTERNALLY: "crit" };
const msg = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Falha de conexão com a API");

export default function Channels() {
  const desk = useDesk();
  const [listings, setListings] = useState<Listing[] | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [gates, setGates] = useState<GateRow[]>([]);
  const [elig, setElig] = useState<{ seller_type: string; items: EligibilityRow[] } | null>(null);
  const [channel, setChannel] = useState<string>("ALL");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canPrepare = desk.can("listings:prepare") && desk.can("proposals:create");
  const [connecting, setConnecting] = useState<string | null>(null);
  const [connectNote, setConnectNote] = useState<string | null>(null);
  const live = !desk.office.simulated;
  const mlAccount = accountFor("MERCADO_LIVRE", accounts, desk.office.simulated);

  // LIVE: o servidor gera a URL de autorização (PKCE no servidor); o proprietário autoriza no site do ML e o ML volta
  // para o callback do servidor. Aqui só abrimos o navegador e depois atualizamos a lista de contas.
  const connectML = async () => {
    setConnecting("MERCADO_LIVRE"); setConnectNote(null);
    try {
      const r = await api.oauthStart(desk.token, "MERCADO_LIVRE");
      const opened = await openExternal(r.authorize_url);
      setConnectNote(opened
        ? `Autorize no site do Mercado Livre que abriu no navegador (vale por ${Math.round(r.expires_in_s / 60)} min) e depois volte aqui.`
        : `Abra este link no navegador para autorizar: ${r.authorize_url}`);
    } catch (e) { setConnectNote(msg(e)); } finally { setConnecting(null); }
  };

  const load = useCallback(() => {
    api.listings(desk.token).then((r) => { setListings(r.items); setError(null); }).catch((e) => setError(msg(e)));
    api.channelAccounts(desk.token).then((r) => setAccounts(r.items)).catch(() => undefined);
    if (desk.can("golive:evaluate")) {
      api.gates(desk.token).then((r) => setGates(r.items)).catch(() => undefined);
      api.eligibility(desk.token).then(setElig).catch(() => undefined);
    }
  }, [desk.token, desk.can]); // permissões chegam com o usuário: sem desk.can a 1ª carga não buscava gates/elegibilidade
  useEffect(() => { if (desk.me) load(); }, [load, desk.refresh, desk.me]);

  const shown = useMemo(() => (listings ?? []).filter((l) => channel === "ALL" || l.channel === channel)
    .filter((l) => !q || `${l.title} ${l.sku}`.toLowerCase().includes(q.toLowerCase())), [listings, channel, q]);
  const selectedListing = listings?.find((l) => l.id === selected) ?? null;

  return (
    <div className="page split-page">
      <div className="page-head">
        <div><h1>Canais de venda</h1><p>Conexão de cada marketplace, anúncios, preço por canal e conformidade. Publicar e pausar passam por aprovação.</p></div>
        <div className="actions">{canPrepare && <button type="button" className="btn primary" disabled={!desk.online} onClick={() => { setPreparing(true); setSelected(null); }}><Plus />Preparar anúncio</button>}</div>
      </div>

      <div className="channel-cards">
        {CHANNELS.map((c) => {
          const h = channelHealth(c, accountFor(c, accounts, desk.office.simulated), desk.office.simulated);
          const g = gates.find((x) => x.code === CHANNEL_GATE[c]);
          const mine = (listings ?? []).filter((l) => l.channel === c);
          return (
            <button key={c} type="button" className={`dk-card channel-card ${channel === c ? "on" : ""}`} aria-pressed={channel === c} onClick={() => setChannel(channel === c ? "ALL" : c)}>
              <div className="row-head"><Store size={16} aria-hidden="true" /><strong>{channelName(c)}</strong></div>
              <div className="chip-row tight">
                <span className={`dk-chip ${h.tone}`}>{h.kind === "problem" ? <AlertTriangle aria-hidden="true" /> : h.kind === "connected" ? <CheckCircle2 aria-hidden="true" /> : null}{h.label}</span>
                {gatePending(g?.state) && <span className="dk-chip warn" title={g ? `Gate ${g.code}: ${g.state}` : "sem informação de homologação"}><ShieldAlert aria-hidden="true" />Homologação pendente</span>}
              </div>
              <span className="faint">{h.detail}</span>
              {elig && (() => {
                const row = elig.items.find((e) => e.channel === c);
                const x = liveExplanation(row, elig.seller_type);
                return <span className={x.simOnly ? "elig-note sim-only" : "elig-note"} title={x.text}>{row?.status && <span className={`dk-chip ${ELIG[row.status].tone}`}>{SELLER_SHORT[elig.seller_type]}: {ELIG[row.status].label}</span>}{x.simOnly ? "Só simulação" : "Elegibilidade comprovada"}</span>;
              })()}
              <span className="faint num">{mine.filter((l) => l.status === "PUBLISHED").length} publicado(s) · {mine.length} anúncio(s)</span>
            </button>
          );
        })}
      </div>

      {elig && (
        <details className="dk-card elig-card" aria-label="Elegibilidade por canal">
          <summary><strong>Elegibilidade por canal — {SELLER_SHORT[elig.seller_type] ?? elig.seller_type}</strong>
            <span className="faint"> · cadastro, venda habitual, API, venda à ordem, envio e fiscal são verificados separadamente</span></summary>
          <div className="table-scroll"><table className="dk-table">
            <thead><tr><th>Canal</th>{Object.values(elig.items.find((e) => e.registered)?.aspects ?? {}).map((a) => <th key={a.label}>{a.label}</th>)}<th>Fontes</th></tr></thead>
            <tbody>{elig.items.map((e) => (
              <tr key={e.channel} className="static">
                <td><strong>{channelName(e.channel)}</strong>{e.restrictions.map((r) => <div key={r} className="faint">{r}</div>)}</td>
                {Object.entries(e.aspects).map(([k, a]) => <td key={k} title={a.note}><span className={`dk-chip ${ELIG[a.status]?.tone ?? ""}`}>{ELIG[a.status]?.label ?? a.status}</span></td>)}
                {!e.registered && <td colSpan={6} className="faint">{e.note}</td>}
                <td>{e.sources.length === 0 ? <span className="faint">—</span> : e.sources.map((s) => (
                  <button key={s.url} type="button" className="dk-link" title={s.kind} onClick={() => void openExternal(s.url)}>fonte ({s.accessed || "s/d"})</button>))}</td>
              </tr>))}</tbody>
          </table></div>
          <p className="faint" style={{ margin: "8px 0 0" }}>Fontes secundárias não comprovam nada: um aspecto só vira "Comprovada" com confirmação oficial registrada pelo proprietário (com MFA). Mesmo comprovado, vender exige as homologações aprovadas.</p>
        </details>
      )}

      {live && desk.can("gates:manage") && (
        <section className="dk-card connect-card" aria-label="Conta do Mercado Livre">
          <div><strong>Conta do Mercado Livre</strong>
            <p className="faint">{mlAccount?.status === "CONNECTED" ? `Conectada (vendedor ${mlAccount.external_account_id}). Reconecte se trocar de conta.`
              : "Conecte a sua conta de vendedor. Nada é publicado sem a homologação CH-ML e a sua aprovação de cada anúncio."}</p>
            {connectNote && <p role="status" className="faint">{connectNote}</p>}</div>
          <div className="actions">
            <button type="button" className="btn" disabled={!desk.online || connecting !== null || !desk.me?.mfa} onClick={() => void connectML()}
              title={desk.me?.mfa ? undefined : "conectar conta exige login com MFA"}>
              <Store />{mlAccount?.status === "CONNECTED" ? "Reconectar" : "Conectar conta real"}</button>
            <button type="button" className="btn ghost" onClick={load}>Atualizar</button>
          </div>
        </section>
      )}

      <div className="filters">
        <label className="search-field"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" placeholder="Buscar anúncio ou SKU" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar anúncios" /></label>
        {channel !== "ALL" && <button type="button" className="btn ghost" onClick={() => setChannel("ALL")}><X />{channelName(channel)}</button>}
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      <div className={`split ${selected || preparing ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Anúncios">
          {listings === null ? <Skeleton /> : shown.length === 0 ? (
            <Empty icon={<Megaphone />} title={listings.length ? "Nenhum anúncio com esse filtro" : "Nenhum anúncio ainda"}>Prepare um anúncio: o backend calcula preço, margem e conformidade.</Empty>
          ) : (
            <table className="dk-table">
              <thead><tr><th>Anúncio</th><th>Canal</th><th>Situação</th><th className="r">Preço</th><th className="r opt">Contribuição</th><th>Conformidade</th></tr></thead>
              <tbody>{shown.map((l) => {
                const b = l.pricing.quote.breakdown;
                const warn = l.compliance.filter((c) => c.severity === "WARNING").length;
                return (
                  <tr key={l.id} aria-selected={selected === l.id} onClick={() => { setSelected(l.id); setPreparing(false); }}>
                    <td><button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setSelected(l.id); setPreparing(false); }} aria-label={`Abrir anúncio ${l.title}`}>{l.title}</button>
                      <div className="faint">{l.sku} · v{l.version}{l.simulated ? " · simulado" : ""}</div></td>
                    <td>{channelName(l.channel)}</td>
                    <td><span className={`dk-chip ${LTONE[l.status] ?? ""}`}>{LISTING_STATUS[l.status] ?? l.status}</span></td>
                    <td className="r num">{formatMinor(l.price_minor, l.currency)}</td>
                    <td className="r num opt">{b ? `${formatMinor(b.contribution_minor)} · ${formatBps(b.margin_bps)}` : "—"}</td>
                    <td>{l.blockers.length > 0 ? <span className="dk-chip crit"><ShieldAlert aria-hidden="true" />{l.blockers.length} impeditivo(s)</span>
                      : warn > 0 ? <span className="dk-chip warn"><AlertTriangle aria-hidden="true" />{warn} aviso(s)</span> : <span className="dk-chip ok"><CheckCircle2 aria-hidden="true" />ok</span>}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          )}
        </section>
        {selectedListing && !preparing && <ListingPane l={selectedListing} onClose={() => setSelected(null)} onChanged={load} />}
        {preparing && <PreparePane onClose={() => setPreparing(false)} onPrepared={(id) => { load(); setPreparing(false); setSelected(id); }} />}
      </div>
    </div>
  );
}

function Quote({ l }: { l: Listing }) {
  const q = l.pricing.quote; const b = q.breakdown; const inp = l.pricing.input;
  return (
    <section aria-label="Composição do preço">
      <h3 className="sec-title">Composição do preço</h3>
      <div className="facts">
        <div><span className="faint">Preço no canal</span><strong className="num">{formatMinor(l.price_minor, l.currency)}</strong>{q.suggested_minor ? <span className="faint num">sugerido {formatMinor(q.suggested_minor)}</span> : null}</div>
        <div><span className="faint">Ponto de equilíbrio</span><strong className="num">{q.breakeven_minor ? formatMinor(q.breakeven_minor) : "—"}</strong></div>
      </div>
      {b && (
        <dl className="dk-kv" style={{ marginTop: 10 }}>
          {inp && <><dt>Custo do fornecedor</dt><dd className="num">{formatMinor(inp.cost_minor)}</dd><dt>Frete estimado</dt><dd className="num">{formatMinor(inp.shipping_minor)}</dd></>}
          {b.commission_minor !== undefined && <><dt>Comissão do canal</dt><dd className="num">{formatMinor(b.commission_minor)}</dd></>}
          {b.payment_fee_minor !== undefined && <><dt>Tarifa de pagamento</dt><dd className="num">{formatMinor(b.payment_fee_minor)}</dd></>}
          {b.tax_minor !== undefined && <><dt>Tributos (indicativo)</dt><dd className="num">{formatMinor(b.tax_minor)}</dd></>}
          {b.returns_minor !== undefined && <><dt>Provisão de devoluções</dt><dd className="num">{formatMinor(b.returns_minor)}</dd></>}
          <dt>Contribuição</dt><dd className="num"><strong>{formatMinor(b.contribution_minor)}</strong> ({formatBps(b.margin_bps)})</dd>
        </dl>
      )}
    </section>
  );
}

function Compliance({ l }: { l: Listing }) {
  return (
    <section aria-label="Conformidade">
      <h3 className="sec-title">Conformidade ({l.compliance.length})</h3>
      {l.compliance.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhum problema apontado.</p> : (
        <ul className="alert-list">{l.compliance.map((i) => (
          <li key={i.code + i.message}><span className={`dk-chip ${i.severity === "BLOCKER" ? "crit" : "warn"}`}>{i.severity === "BLOCKER" ? <ShieldAlert aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}{i.severity === "BLOCKER" ? "Impeditivo" : "Aviso"}</span>{i.message}</li>
        ))}</ul>
      )}
    </section>
  );
}

function ListingPane({ l, onClose, onChanged }: { l: Listing; onClose(): void; onChanged(): void }) {
  const desk = useDesk();
  const [busy, setBusy] = useState(false);
  const [pausing, setPausing] = useState(false);
  const can = desk.can("proposals:create");
  const publishable = l.blockers.length === 0 && ["DRAFT", "ERROR"].includes(l.status);
  async function publish() {
    setBusy(true);
    try { await api.proposePublish(desk.token, l.id); desk.notify("Publicação proposta — aguarda decisão na Central de Aprovações."); onChanged(); }
    catch (e) { desk.notify(msg(e), "crit"); } finally { setBusy(false); }
  }
  return (
    <aside className="dk-card detail-pane" aria-label="Detalhe do anúncio">
      <div className="detail-head">
        <div style={{ minWidth: 0 }}><div className="faint">{channelName(l.channel)} · {l.sku} · v{l.version}{l.simulated ? " · simulado" : ""}</div><h2 className="detail-title">{l.title}</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar anúncio"><X size={18} /></button>
      </div>
      <div className="detail-body">
        <div className="chip-row"><span className={`dk-chip ${LTONE[l.status] ?? ""}`}>{LISTING_STATUS[l.status] ?? l.status}</span>{l.external_id && <span className="dk-chip">no canal: {l.external_id}</span>}</div>
        {l.pricing.quote.warnings.length > 0 && <div className="callout warn" role="note">{l.pricing.quote.warnings.map((w) => <div key={w}>{w}</div>)}</div>}
        <Quote l={l} />
        <Compliance l={l} />
      </div>
      {can && (publishable || l.status === "PUBLISHED") && (
        <div className="detail-foot"><div className="decision-bar">
          {publishable && <button type="button" className="btn primary" disabled={busy || !desk.online} onClick={publish}><Send />Propor publicação</button>}
          {l.status === "PUBLISHED" && <button type="button" className="btn" disabled={busy || !desk.online} onClick={() => setPausing(true)}><PauseCircle />Propor pausa</button>}
          <span className="faint">Vai para a Central de Aprovações; nada muda no canal antes da decisão.</span>
        </div></div>
      )}
      {!can && l.blockers.length === 0 && l.status === "DRAFT" && <div className="detail-foot faint">Seu papel não cria propostas.</div>}
      {pausing && <ConfirmDialog title="Propor pausa deste anúncio?" needReason confirmLabel="Propor pausa" body="A pausa só acontece no canal depois de aprovada."
        onCancel={() => setPausing(false)} onConfirm={async (r) => { await api.proposePause(desk.token, l.id, r); setPausing(false); desk.notify("Pausa proposta — aguarda aprovação."); onChanged(); }} />}
    </aside>
  );
}

function PreparePane({ onClose, onPrepared }: { onClose(): void; onPrepared(id: string): void }) {
  const desk = useDesk();
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierSummary[]>([]);
  const [productId, setProductId] = useState("");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [variantId, setVariantId] = useState("");
  const [offerId, setOfferId] = useState("");
  const [channel, setChannel] = useState<string>("WOOCOMMERCE");
  const [shipping, setShipping] = useState("15,00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([api.products(desk.token), api.suppliers(desk.token)]).then(([p, s]) => { setProducts(p.items); setSuppliers(s.items); }).catch((e) => setError(msg(e)));
  }, [desk.token]);
  useEffect(() => {
    if (!productId) { setOffers([]); return; }
    api.product(desk.token, productId).then((p) => { setOffers(p.offers); setVariantId(p.variants[0]?.id ?? ""); }).catch(() => setOffers([]));
  }, [productId, desk.token]);
  const variants = products.find((p) => p.id === productId)?.variants ?? [];
  const vOffers = offers.filter((o) => o.variant_id === variantId && o.status === "ACTIVE");
  const sup = (id: string) => suppliers.find((s) => s.id === id);
  async function prepare() {
    setBusy(true); setError(null);
    try {
      const cents = Math.round(Number(shipping.replace(/\./g, "").replace(",", ".")) * 100);
      if (!Number.isFinite(cents) || cents < 0) throw new Error("Frete inválido.");
      const l = await api.prepareListing(desk.token, channel, { variant_id: variantId, offer_id: offerId, shipping_minor: cents });
      desk.notify("Anúncio preparado: preço e conformidade calculados pelo servidor.");
      onPrepared(l.id);
    } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  }
  return (
    <aside className="dk-card detail-pane" aria-label="Preparar anúncio">
      <div className="detail-head"><div><div className="faint">Assistente</div><h2 className="detail-title">Preparar anúncio</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar assistente"><X size={18} /></button></div>
      <div className="detail-body">
        <label className="stack-field"><span className="faint">1. Produto do catálogo</span>
          <select className="field" value={productId} onChange={(e) => { setProductId(e.target.value); setOfferId(""); }}>
            <option value="">Selecione…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
        {variants.length > 0 && <label className="stack-field"><span className="faint">2. Variante</span>
          <select className="field" value={variantId} onChange={(e) => { setVariantId(e.target.value); setOfferId(""); }}>{variants.map((v) => <option key={v.id} value={v.id}>{v.sku}</option>)}</select></label>}
        {productId && <label className="stack-field"><span className="faint">3. Oferta do fornecedor</span>
          <select className="field" value={offerId} onChange={(e) => setOfferId(e.target.value)}>
            <option value="">{vOffers.length ? "Selecione…" : "Nenhuma oferta ativa para esta variante"}</option>
            {vOffers.map((o) => <option key={o.id} value={o.id}>{sup(o.supplier_id)?.name ?? o.supplier_id.slice(0, 8)} · {formatMinor(o.cost_minor)} · estoque {o.stock_qty}{sup(o.supplier_id)?.status !== "APPROVED" ? " (fornecedor não aprovado)" : ""}</option>)}
          </select></label>}
        <label className="stack-field"><span className="faint">4. Canal</span>
          <select className="field" value={channel} onChange={(e) => setChannel(e.target.value)}>{CHANNELS.map((c) => <option key={c} value={c}>{channelName(c)}</option>)}</select></label>
        <label className="stack-field"><span className="faint">5. Frete estimado (R$)</span>
          <input className="field" inputMode="decimal" value={shipping} onChange={(e) => setShipping(e.target.value)} /></label>
        <p className="faint" style={{ margin: 0 }}>O servidor calcula preço sugerido, equilíbrio, margem e conformidade. Nada é publicado aqui.</p>
        {error && <p role="alert" className="dk-error">{error}</p>}
      </div>
      <div className="detail-foot"><button type="button" className="btn primary" disabled={busy || !variantId || !offerId || !desk.online} onClick={prepare}>{busy ? "Calculando…" : "Calcular preço e conformidade"}</button></div>
    </aside>
  );
}
