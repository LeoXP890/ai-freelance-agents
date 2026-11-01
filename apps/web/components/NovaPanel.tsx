"use client";
// Painel da Nova (loja e canais): preparar anúncio (preço + compliance calculados no backend) e propor publicação.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Listing, type Offer, type ProductSummary, type SupplierSummary } from "@/lib/api";
import { formatBps, formatMinor, LISTING_STATUS } from "@/lib/format";

const CHANNELS = ["WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"] as const;

export default function NovaPanel({ token, canPrepare, refreshSignal, onProposed }: {
  token: string; canPrepare: boolean; refreshSignal: number; onProposed(): void;
}) {
  const [listings, setListings] = useState<Listing[]>([]);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierSummary[]>([]);
  const [productId, setProductId] = useState("");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [variantId, setVariantId] = useState("");
  const [offerId, setOfferId] = useState("");
  const [channel, setChannel] = useState<(typeof CHANNELS)[number]>("WOOCOMMERCE");
  const [shipping, setShipping] = useState("15,00");
  const [prepared, setPrepared] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, p, s] = await Promise.all([api.listings(token), api.products(token), api.suppliers(token)]);
      setListings(l.items);
      setProducts(p.items);
      setSuppliers(s.items);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha de conexão com a API");
    }
  }, [token]);
  useEffect(() => { void load(); }, [load, refreshSignal]);

  useEffect(() => {
    if (!productId) { setOffers([]); return; }
    api.product(token, productId).then((p) => {
      setOffers(p.offers);
      setVariantId(p.variants[0]?.id ?? "");
    }).catch(() => setOffers([]));
  }, [productId, token]);

  const supplierName = (id: string) => suppliers.find((s) => s.id === id)?.name ?? id.slice(0, 8);
  const supplierStatus = (id: string) => suppliers.find((s) => s.id === id)?.status;
  const variantOffers = offers.filter((o) => o.variant_id === variantId && o.status === "ACTIVE");
  const variants = products.find((p) => p.id === productId)?.variants ?? [];

  async function prepare() {
    setBusy(true);
    setError(null);
    try {
      const cents = Math.round(Number(shipping.replace(/\./g, "").replace(",", ".")) * 100);
      if (!Number.isFinite(cents) || cents < 0) throw new Error("frete inválido");
      setPrepared(await api.prepareListing(token, channel, { variant_id: variantId, offer_id: offerId, shipping_minor: cents }));
      void load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function propose(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.proposePublish(token, id);
      onProposed();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha de conexão com a API");
    } finally {
      setBusy(false);
    }
  }

  const q = prepared?.pricing.quote;
  return (
    <div className="dept">
      <h3>Anúncios</h3>
      {listings.length === 0 ? <p className="muted small">Nenhum anúncio preparado.</p> : (
        <ul className="inbox">
          {listings.map((l) => (
            <li key={l.id} className="inbox-item static">
              <span className="inbox-title">{l.title} <span className="muted small">({l.sku})</span></span>
              <span className="inbox-meta">
                <span>{l.channel}</span><span>{LISTING_STATUS[l.status] ?? l.status}</span>
                <span>{formatMinor(l.price_minor)}</span><span className="muted">v{l.version}</span>
                {l.blockers.length > 0 && <span className="err">{l.blockers.length} impeditivo(s)</span>}
                {l.simulated && <span className="sim">Simulado</span>}
              </span>
              {canPrepare && l.blockers.length === 0 && ["DRAFT", "ERROR"].includes(l.status) && (
                <button type="button" disabled={busy} onClick={() => propose(l.id)}>Propor publicação</button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canPrepare && (
        <details className="prepare">
          <summary>Preparar novo anúncio</summary>
          <div className="stack">
            <label>Produto
              <select value={productId} onChange={(e) => { setProductId(e.target.value); setOfferId(""); setPrepared(null); }}>
                <option value="">Selecione…</option>
                {products.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
              </select>
            </label>
            {variants.length > 0 && (
              <label>Variante
                <select value={variantId} onChange={(e) => { setVariantId(e.target.value); setOfferId(""); }}>
                  {variants.map((v) => <option key={v.id} value={v.id}>{v.sku}</option>)}
                </select>
              </label>
            )}
            {productId && (
              <label>Oferta do fornecedor
                <select value={offerId} onChange={(e) => setOfferId(e.target.value)}>
                  <option value="">Selecione…</option>
                  {variantOffers.map((o) => (
                    <option key={o.id} value={o.id}>
                      {supplierName(o.supplier_id)} · {formatMinor(o.cost_minor)} · estoque {o.stock_qty}
                      {supplierStatus(o.supplier_id) !== "APPROVED" ? " (fornecedor não aprovado)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>Canal
              <select value={channel} onChange={(e) => setChannel(e.target.value as (typeof CHANNELS)[number])}>
                {CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label>Frete estimado (R$)<input inputMode="decimal" value={shipping} onChange={(e) => setShipping(e.target.value)} /></label>
            <button type="button" className="primary" disabled={busy || !variantId || !offerId} onClick={prepare}>
              {busy ? "Calculando…" : "Calcular preço e checar conformidade"}</button>
          </div>
        </details>
      )}

      {prepared && q && (
        <div className="quote" aria-live="polite">
          <h4>{prepared.title} · {prepared.channel} · v{prepared.version}</h4>
          <dl className="kv">
            <dt>Preço</dt><dd>{formatMinor(prepared.price_minor)}</dd>
            <dt>Ponto de equilíbrio</dt><dd>{q.breakeven_minor ? formatMinor(q.breakeven_minor) : "—"}</dd>
            {q.breakdown && <><dt>Contribuição</dt>
              <dd>{formatMinor(q.breakdown.contribution_minor)} ({formatBps(q.breakdown.margin_bps)})</dd></>}
          </dl>
          <ul className="issues">
            {prepared.compliance.map((i) => (
              <li key={i.code + i.message} className={i.severity === "BLOCKER" ? "err" : "warn"}>
                <strong>{i.severity === "BLOCKER" ? "Impeditivo" : "Aviso"}:</strong> {i.message}
              </li>
            ))}
          </ul>
          {prepared.blockers.length === 0 ? (
            <button type="button" className="primary" disabled={busy} onClick={() => propose(prepared.id)}>
              Propor publicação (vai para a sala do CEO)</button>
          ) : <p className="err small">Resolva os impeditivos antes de propor.</p>}
        </div>
      )}
      {error && <p className="err" role="alert">{error}</p>}
    </div>
  );
}
