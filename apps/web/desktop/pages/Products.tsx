"use client";
// UX-06 Produtos & pesquisa (UXD-012): catálogo mestre (tabela + editor com versão otimista, mídia com texto
// alternativo, histórico imutável, arquivar/reativar com motivo) e oportunidades da Luna com evidência e fontes.
// Imagens externas não são carregadas dentro do app (CSP): mostramos a URL e o texto alternativo.
import { AlertTriangle, ArrowDown, ArrowUp, Archive, FlaskConical, ImagePlus, Lightbulb, PackageSearch, RotateCcw, Save, Search, Send, Trash2, Upload, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type Offer, type OpportunityRow, type ProductFull, type ProductSummary } from "@/lib/api";
import { since } from "@/lib/agentIdentity";
import { formatMinor, hoursSince } from "@/lib/format";
import { Meter } from "../charts";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { ConfirmDialog, Empty } from "../ui";

type Tab = "catalog" | "research";
const FIELD: Record<string, string> = { title: "título", description: "descrição", category: "categoria", brand: "marca", ncm: "NCM", media: "imagens",
  regulated: "regulado", status: "situação", created: "criação" };
const EVIDENCE: Record<string, string> = { HIGH: "alta", MEDIUM: "média", LOW: "baixa", ALTA: "alta", MEDIA: "média", BAIXA: "baixa" };
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function Products() {
  const desk = useDesk();
  const [tab, setTab] = useState<Tab>("catalog");
  return (
    <div className="page split-page">
      <div className="page-head">
        <div><h1>Produtos & pesquisa</h1><p>Catálogo mestre (fonte da verdade dos anúncios) e oportunidades levantadas pela Luna com evidências.</p></div>
        <div className="actions"><div className="dk-seg" role="tablist" aria-label="Seção">
          <button type="button" role="tab" aria-selected={tab === "catalog"} aria-pressed={tab === "catalog"} onClick={() => setTab("catalog")}><PackageSearch size={14} /> Catálogo</button>
          <button type="button" role="tab" aria-selected={tab === "research"} aria-pressed={tab === "research"} onClick={() => setTab("research")}><Lightbulb size={14} /> Oportunidades</button>
        </div></div>
      </div>
      {tab === "catalog" ? <Catalog /> : <Research onProposed={() => desk.go("approvals")} />}
    </div>
  );
}

function Catalog() {
  const desk = useDesk();
  const [items, setItems] = useState<ProductSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api.products(desk.token).then((r) => { setItems(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [desk.token]);
  useEffect(() => { if (desk.me) load(); }, [load, desk.refresh, desk.me]);
  const shown = useMemo(() => (items ?? []).filter((p) => archived || p.status !== "ARCHIVED")
    .filter((p) => !q || `${p.title} ${p.variants.map((v) => v.sku).join(" ")}`.toLowerCase().includes(q.toLowerCase())), [items, q, archived]);
  return (
    <>
      <div className="filters">
        <label className="search-field"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" placeholder="Buscar produto ou SKU" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar produtos" /></label>
        <label className="inline-field"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> <span className="faint">mostrar arquivados</span></label>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      <div className={`split ${selected ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Catálogo mestre">
          {items === null ? <Skeleton /> : shown.length === 0 ? <Empty icon={<PackageSearch />} title="Nenhum produto com esse filtro" /> : (
            <table className="dk-table">
              <thead><tr><th>Produto</th><th>Variantes</th><th>Situação</th></tr></thead>
              <tbody>{shown.map((p) => (
                <tr key={p.id} aria-selected={selected === p.id} onClick={() => setSelected(p.id)}>
                  <td><button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setSelected(p.id); }} aria-label={`Abrir produto ${p.title}`}>{p.title}</button>
                    <div className="faint">{p.simulated ? "simulado" : "real"}</div></td>
                  <td className="faint">{p.variants.map((v) => v.sku).join(", ")}</td>
                  <td><span className={`dk-chip ${p.status === "ARCHIVED" ? "" : "ok"}`}>{p.status === "ARCHIVED" ? "Arquivado" : "Ativo"}</span></td>
                </tr>))}</tbody>
            </table>
          )}
        </section>
        {selected && <ProductPane id={selected} onClose={() => setSelected(null)} onChanged={load} />}
      </div>
    </>
  );
}

function ProductPane({ id, onClose, onChanged }: { id: string; onClose(): void; onChanged(): void }) {
  const desk = useDesk();
  const [p, setP] = useState<ProductFull | null>(null);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [hist, setHist] = useState<{ version: number; changed_fields: string[]; changed_by: string; at: string }[]>([]);
  const [form, setForm] = useState({ title: "", description: "", category: "", ncm: "" });
  const [media, setMedia] = useState<{ url: string; alt: string }[]>([]);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const write = desk.can("catalog:write");
  // Fotos do computador: o servidor regrava (sem GPS/EXIF) e devolve a URL pública; entram no fim da lista e só
  // ficam no produto ao salvar (mesmo fluxo versionado). O texto alternativo continua obrigatório.
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true); setUploadNote(null);
    const added: { url: string; alt: string }[] = [];
    const failed: string[] = [];
    for (const f of Array.from(files).slice(0, Math.max(0, 10 - media.length))) {
      try { added.push({ url: (await api.uploadMedia(desk.token, f)).url, alt: "" }); }
      catch (e) { failed.push(`${f.name}: ${e instanceof Error ? e.message : String(e)}`); }
    }
    setMedia((m) => [...m, ...added]);
    setUploading(false);
    setUploadNote([added.length ? `${added.length} foto(s) enviada(s) — descreva cada uma e salve.` : "", ...failed].filter(Boolean).join(" · "));
  };
  const load = useCallback(async () => {
    try {
      const [full, h, withOffers] = await Promise.all([api.productFull(desk.token, id), api.productVersions(desk.token, id), api.product(desk.token, id)]);
      setP(full); setHist(h.items); setOffers(withOffers.offers); setConflict(false); setError(null);
      setForm({ title: full.title, description: full.description, category: full.category, ncm: full.ncm ?? "" });
      setMedia(full.media.map((m) => ({ ...m })));
    } catch (e) { setError(msg(e)); }
  }, [desk.token, id]);
  useEffect(() => { setP(null); void load(); }, [load]);
  const dirty = !!p && (form.title !== p.title || form.description !== p.description || form.category !== p.category || form.ncm !== (p.ncm ?? "")
    || JSON.stringify(media) !== JSON.stringify(p.media));
  const ro = !write || p?.status === "ARCHIVED";
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!p) return;
    try {
      const r = await api.updateProduct(desk.token, p.id, { expected_version: p.version, ...form, ncm: form.ncm || undefined, media });
      desk.notify(r.changed.length ? `Salvo como v${r.version} (${r.changed.map((c) => FIELD[c] ?? c).join(", ")})${r.listings_marked ? ` · ${r.listings_marked} anúncio(s) precisam ser preparados de novo` : ""}` : "Nada mudou.");
      await load(); onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.code === "VERSION_CONFLICT") setConflict(true);
      setError(msg(err));
    }
  }
  const move = (i: number, d: -1 | 1) => setMedia((m) => { const j = i + d; if (j < 0 || j >= m.length) return m; return m.map((x, k) => (k === i ? m[j] : k === j ? m[i] : x)) as typeof m; });
  return (
    <aside className="dk-card detail-pane" aria-label="Detalhe do produto">
      <div className="detail-head">
        <div style={{ minWidth: 0 }}><div className="faint">{p ? `SKU ${p.sku_base} · v${p.version} · ${p.status === "ARCHIVED" ? "arquivado" : "ativo"}${p.simulated ? " · simulado" : ""}` : "Produto"}</div>
          <h2 className="detail-title">{p?.title ?? "Carregando…"}</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar produto"><X size={18} /></button>
      </div>
      {p && (
        <form className="detail-body" onSubmit={save} id="product-form">
          {conflict && <div className="callout warn" role="alert">Outra pessoa salvou uma versão mais nova. <button type="button" className="dk-link" onClick={() => void load()}>Recarregar a versão atual (descarta suas mudanças)</button></div>}
          {error && !conflict && <p role="alert" className="dk-error">{error}</p>}
          <label className="stack-field"><span className="faint">Título</span><input className="field" value={form.title} disabled={ro} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          <label className="stack-field"><span className="faint">Descrição</span><textarea className="field" rows={3} value={form.description} disabled={ro} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <div className="facts">
            <label className="stack-field"><span className="faint">Categoria</span><input className="field" value={form.category} disabled={ro} onChange={(e) => setForm({ ...form, category: e.target.value })} /></label>
            <label className="stack-field"><span className="faint">NCM</span><input className="field" inputMode="numeric" maxLength={8} value={form.ncm} disabled={ro} onChange={(e) => setForm({ ...form, ncm: e.target.value.replace(/\D/g, "") })} /></label>
          </div>
          {p.regulated && <div className="callout warn" role="note"><AlertTriangle size={16} aria-hidden="true" /> Produto regulado: exige revisão de compliance antes de anunciar.</div>}
          <fieldset className="dk-fieldset" disabled={ro}>
            <h3 className="sec-title">Imagens ({media.length}) <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>a primeira é a capa</span></h3>
            <div style={{ display: "grid", gap: 6 }}>{media.map((m, i) => (
              <div key={i} className="media-edit">
                {/^https?:\/\/\S+\.(jpe?g|png|webp)(\?\S*)?$/i.test(m.url)
                  ? <img className="thumb" src={m.url} alt="" loading="lazy" /> : <span className="thumb" aria-hidden="true" />}
                <input className="field" aria-label={`URL da imagem ${i + 1}`} placeholder="https://…/foto.webp" value={m.url} onChange={(e) => setMedia(media.map((x, k) => (k === i ? { ...x, url: e.target.value } : x)))} />
                <input className="field" aria-label={`Texto alternativo da imagem ${i + 1}`} placeholder="Descreva a imagem" value={m.alt} onChange={(e) => setMedia(media.map((x, k) => (k === i ? { ...x, alt: e.target.value } : x)))} />
                <button type="button" className="icon-btn" aria-label={`Subir imagem ${i + 1}`} onClick={() => move(i, -1)}><ArrowUp size={14} /></button>
                <button type="button" className="icon-btn" aria-label={`Descer imagem ${i + 1}`} onClick={() => move(i, 1)}><ArrowDown size={14} /></button>
                <button type="button" className="icon-btn" aria-label={`Remover imagem ${i + 1}`} onClick={() => setMedia(media.filter((_, k) => k !== i))}><Trash2 size={14} /></button>
              </div>))}</div>
            {media.length < 10 && <div className="chip-row tight" style={{ marginTop: 6 }}>
              <label className={`btn ghost ${uploading ? "busy" : ""}`}><Upload size={14} aria-hidden="true" />{uploading ? "Enviando…" : "Enviar fotos"}
                <input type="file" accept="image/jpeg,image/png,image/webp" multiple hidden disabled={uploading || !desk.online}
                  aria-label="Enviar fotos do computador" onChange={(e) => { void upload(e.target.files); e.target.value = ""; }} /></label>
              <button type="button" className="dk-link" onClick={() => setMedia([...media, { url: "", alt: "" }])}><ImagePlus size={14} />Adicionar por URL</button>
            </div>}
            {uploadNote && <p role="status" className="faint">{uploadNote}</p>}
          </fieldset>
          <section aria-label="Variantes e ofertas">
            <h3 className="sec-title">Variantes e ofertas</h3>
            <table className="dk-table"><tbody>{p.variants.map((v) => {
              const vo = offers.filter((o) => o.variant_id === v.id);
              return (
                <tr key={v.id} className="static"><td><strong>{v.sku}</strong><div className="faint">{Object.entries(v.attributes).map(([k, x]) => `${k}: ${x}`).join(" · ")}</div></td>
                  <td>{vo.length === 0 ? <span className="faint">sem oferta</span> : vo.map((o) => { const h = hoursSince(o.stock_observed_at); return (
                    <div key={o.id} className="num" style={{ fontSize: 12 }}>{formatMinor(o.cost_minor)} · estoque {o.stock_qty} {h > 24 ? <span className="dk-chip warn">obsoleto {h} h</span> : <span className="faint">· há {h} h</span>}</div>); })}</td></tr>
              );
            })}</tbody></table>
          </section>
          <section aria-label="Histórico de versões">
            <h3 className="sec-title">Histórico</h3>
            <ol className="timeline">{hist.map((h) => (
              <li key={h.version}><time className="faint num">v{h.version}</time><span className="dot" aria-hidden="true" />
                <span>{h.changed_fields.map((c) => FIELD[c] ?? c).join(", ")} <span className="faint">· {new Date(h.at).toLocaleString("pt-BR")}</span></span></li>))}</ol>
          </section>
        </form>
      )}
      {p && write && (
        <div className="detail-foot"><div className="decision-bar">
          <button type="submit" form="product-form" className="btn primary" disabled={!dirty || ro || !desk.online}><Save />Salvar nova versão</button>
          <button type="button" className="btn" disabled={!desk.online} onClick={() => setArchiving(true)}>{p.status === "ARCHIVED" ? <><RotateCcw />Reativar</> : <><Archive />Arquivar</>}</button>
          {dirty && <span className="faint">Alterações não salvas.</span>}
        </div></div>
      )}
      {archiving && p && <ConfirmDialog title={p.status === "ARCHIVED" ? "Reativar produto?" : "Arquivar produto?"} needReason danger={p.status !== "ARCHIVED"}
        confirmLabel={p.status === "ARCHIVED" ? "Reativar" : "Arquivar"} body={p.status === "ARCHIVED" ? "Volta a poder ser anunciado." : "O servidor recusa se houver anúncio ativo deste produto."}
        onCancel={() => setArchiving(false)} onConfirm={async (r) => { await api.setProductArchived(desk.token, p.id, p.status !== "ARCHIVED", r); setArchiving(false); desk.notify("Situação do produto atualizada."); await load(); onChanged(); }} />}
    </aside>
  );
}

function Research({ onProposed }: { onProposed(): void }) {
  const desk = useDesk();
  const [items, setItems] = useState<OpportunityRow[] | null>(null);
  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api.opportunities(desk.token).then((r) => { setItems(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [desk.token]);
  useEffect(() => { if (desk.me) load(); }, [load, desk.refresh, desk.me]);
  const luna = desk.office.agents.luna;
  return (
    <>
      {desk.can("tasks:create") && (
        <form className="dk-card row-actions" style={{ marginBottom: 16 }} onSubmit={async (e) => {
          e.preventDefault();
          try { await api.createTask(desk.token, "luna", "research.opportunities", { topic: topic.trim() }); setTopic(""); desk.notify("Pesquisa pedida à Luna — o resultado aparece aqui quando ela concluir."); }
          catch (err) { setError(msg(err)); }
        }}>
          <FlaskConical size={18} aria-hidden="true" />
          <input className="field" style={{ flex: 1, minWidth: 220 }} required minLength={3} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Tema para a Luna pesquisar (ex.: garrafas térmicas para academia)" aria-label="Tema da pesquisa" />
          <button type="submit" className="btn primary" disabled={!desk.online}>Pedir pesquisa</button>
          {luna && <span className="faint">Luna: {luna.state === "IDLE" ? "disponível" : luna.state.toLowerCase()} · {since(luna.last_event_at)}</span>}
        </form>
      )}
      {error && <p role="alert" className="dk-error">{error}</p>}
      {items === null ? <Skeleton /> : items.length === 0 ? <Empty icon={<Lightbulb />} title="Nenhuma oportunidade ainda">Peça uma pesquisa à Luna acima.</Empty> : (
        <div className="opp-grid">{items.map((o) => (
          <article key={o.id} className="dk-card opp" aria-label={o.title}>
            <div className="row-head"><strong>{o.title}</strong>{o.simulated && <span className="dk-chip demo">Simulado</span>}<span className="dk-chip" style={{ marginLeft: "auto" }}>{o.status}</span></div>
            <div className="faint">{o.category}</div>
            <div className="row-head" style={{ marginTop: 6 }}><span className="num"><strong>{o.score}</strong>/100</span><span style={{ flex: 1 }}><Meter used={o.score} total={100} label={`Nota de ${o.title}`} progress /></span>
              <span className="faint">evidência {EVIDENCE[o.evidence_quality] ?? o.evidence_quality.toLowerCase()}</span></div>
            <p style={{ margin: "6px 0" }}>{o.rationale}</p>
            {o.contraindications.length > 0 && <div className="callout warn" role="note"><AlertTriangle size={14} aria-hidden="true" /> {o.contraindications.join("; ")}</div>}
            <div className="chip-row tight">{o.sources.map((s) => <span key={s.id} className="dk-chip info" title={s.url}>{s.title}{s.date ? ` · ${new Date(s.date).toLocaleDateString("pt-BR")}` : ""}{s.simulated ? " · simulada" : ""}</span>)}</div>
            {desk.can("proposals:create") && o.status === "NEW" && (
              <button type="button" className="btn" style={{ marginTop: 10 }} disabled={!desk.online} onClick={async () => {
                try { await api.opportunityProposal(desk.token, o.id); desk.notify("Rascunho de anúncio proposto — aguarda aprovação."); load(); onProposed(); } catch (e) { setError(msg(e)); }
              }}><Send />Propor rascunho de anúncio</button>
            )}
          </article>
        ))}</div>
      )}
    </>
  );
}
