"use client";
// UX-09 Base de conhecimento (UXD-014): busca híbrida (a mesma que os agentes usam, com ACL), filtros por coleção
// e situação, fonte, validade e dono da revisão. Revisão de documento passa pelo backend com justificativa.
import { BookOpen, ExternalLink, Search, ShieldAlert } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, type KbDoc, type KbHit } from "@/lib/api";
import { useDesk } from "../context";
import { openExternal } from "../native";
import { DataTable, Skeleton } from "../table";
import { ConfirmDialog, Empty } from "../ui";

const STATUS: Record<string, { label: string; tone: string }> = {
  APPROVED: { label: "Aprovado", tone: "ok" }, PENDING_REVIEW: { label: "Aguardando revisão", tone: "warn" },
  REJECTED: { label: "Rejeitado", tone: "crit" },
};
const chip = (s: string) => <span className={`dk-chip ${STATUS[s]?.tone ?? ""}`}>{STATUS[s]?.label ?? s}</span>;
const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("pt-BR") : "—");

export default function Knowledge() {
  const desk = useDesk();
  const [docs, setDocs] = useState<KbDoc[] | null>(null);
  const [collections, setCollections] = useState<string[]>([]);
  const [collection, setCollection] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ q: string; route: string; items: KbHit[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<{ doc: KbDoc; approved: boolean } | null>(null);

  const load = useCallback(() => {
    api.kbDocuments(desk.token, { collection, approval_status: status })
      .then((r) => { setDocs(r.items); setCollections(r.collections); setError(null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha de conexão com a API"));
  }, [desk.token, collection, status]);
  useEffect(() => { load(); }, [load, desk.refresh]);

  async function search(e: FormEvent) {
    e.preventDefault();
    if (q.trim().length < 2) { setHits(null); return; }
    try {
      const r = await api.kbSearch(desk.token, q.trim(), collection || undefined);
      setHits({ q: q.trim(), route: r.route, items: r.items });
    } catch (err) { setError(err instanceof ApiError ? err.message : "Falha de conexão com a API"); }
  }

  const expired = docs?.filter((d) => d.expired).length ?? 0;
  const pendingReview = docs?.filter((d) => d.approval_status === "PENDING_REVIEW").length ?? 0;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Base de conhecimento</h1>
          <p>Documentos normativos e de mercado que os agentes consultam — com fonte, validade e revisão.</p>
        </div>
      </div>
      <form className="filters" onSubmit={search} role="search">
        <label className="search-field wide"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Pergunte como um agente: ex. prazo de devolução por arrependimento"
            aria-label="Buscar no conteúdo dos documentos" /></label>
        <button type="submit" className="btn">Buscar</button>
        <select className="field" value={collection} onChange={(e) => setCollection(e.target.value)} aria-label="Coleção">
          <option value="">Todas as coleções</option>
          {collections.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="field" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Situação">
          <option value="">Qualquer situação</option>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <span className="faint" style={{ marginLeft: "auto" }}>{docs ? `${docs.length} documentos · ${pendingReview} aguardando revisão · ${expired} vencidos` : ""}</span>
      </form>
      {error && <p role="alert" className="dk-error">{error}</p>}

      {hits && (
        <section className="dk-card" aria-label="Resultados da busca" style={{ marginBottom: 16 }}>
          <div className="card-title"><Search size={16} />Trechos para “{hits.q}” <span className="faint">rota {hits.route}</span>
            <button type="button" className="dk-link" onClick={() => setHits(null)}>Limpar</button></div>
          {hits.items.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nada encontrado nos documentos que você pode ler.</p> : (
            <ol className="hits">{hits.items.map((h, i) => (
              <li key={`${h.document_id}-${i}`}>
                <div className="step-head"><strong>{h.title}</strong>{chip(h.approval_status)}<span className="faint">{h.collection} · relevância {h.score.toFixed(2)}</span></div>
                <p style={{ margin: "4px 0 0" }}>{h.text}</p>
              </li>))}</ol>
          )}
        </section>
      )}

      <section className="dk-card table-card" aria-label="Documentos">
        {docs === null ? <Skeleton /> : docs.length === 0 ? (
          <Empty icon={<BookOpen />} title="Nenhum documento com esse filtro">Documentos entram pela API de ingestão (com fonte e política de acesso obrigatórias).</Empty>
        ) : (
          <DataTable id="kb" label="Documentos da base" rows={docs} rowKey={(d) => d.id} initialSort={{ key: "title", dir: 1 }} columns={[
            { key: "title", label: "Documento", sort: (d) => d.title, render: (d) => <>
              <strong>{d.title}</strong>
              <div className="faint">{d.publisher} · {d.document_type} · v{d.version}{d.simulated ? " · simulado" : ""}</div>
              {d.suspicious && <div className="chip-row tight"><span className="dk-chip crit"><ShieldAlert aria-hidden="true" />Conteúdo suspeito (possível injeção)</span></div>}</> },
            { key: "collection", label: "Coleção", sort: (d) => d.collection, render: (d) => d.collection },
            { key: "status", label: "Situação", sort: (d) => d.approval_status, render: (d) => chip(d.approval_status) },
            { key: "valid", label: "Validade", optional: true, sort: (d) => d.valid_until ?? "9999", render: (d) => <>
              {d.valid_until ? <span className={d.expired ? "dk-chip crit" : ""}>{d.expired ? "Vencido em " : "até "}{date(d.valid_until)}</span> : <span className="faint">sem prazo</span>}
              <div className="faint">verificado {date(d.verified_at)}</div></> },
            { key: "owner", label: "Dono da revisão", optional: true, sort: (d) => d.owner_email ?? d.owner, render: (d) => <span className="ellipsis" style={{ display: "inline-block", maxWidth: 180 }}>{d.owner_email ?? d.owner}</span> },
            { key: "source", label: "Fonte", optional: true, render: (d) => /^https?:\/\//.test(d.source_url)
              ? <a className="dk-link" href={d.source_url} target="_blank" rel="noreferrer noopener"
                  onClick={(e) => { e.preventDefault(); void openExternal(d.source_url as string); }}>Abrir <ExternalLink size={12} /></a>
              : <span className="faint" title={d.source_url}>{d.source_url.split("/")[0]}//…</span> },
            ...(desk.can("kb:approve") ? [{ key: "act", label: "Revisão", render: (d: KbDoc) => d.approval_status === "PENDING_REVIEW" ? (
              <span className="row-actions tight">
                <button type="button" className="btn" disabled={!desk.online} onClick={() => setReviewing({ doc: d, approved: true })}>Aprovar</button>
                <button type="button" className="btn danger" disabled={!desk.online} onClick={() => setReviewing({ doc: d, approved: false })}>Rejeitar</button>
              </span>) : null }] : []),
          ]} />
        )}
      </section>
      {reviewing && (
        <ConfirmDialog title={reviewing.approved ? "Aprovar documento para uso dos agentes?" : "Rejeitar documento?"}
          danger={!reviewing.approved} needReason confirmLabel={reviewing.approved ? "Aprovar documento" : "Rejeitar documento"}
          body={<>“{reviewing.doc.title}” ({reviewing.doc.collection}). A decisão fica registrada na auditoria.</>}
          onCancel={() => setReviewing(null)}
          onConfirm={async (note) => {
            await api.kbReview(desk.token, reviewing.doc.id, reviewing.approved, note);
            setReviewing(null); load(); desk.notify("Revisão registrada no servidor.");
          }} />
      )}
    </div>
  );
}
