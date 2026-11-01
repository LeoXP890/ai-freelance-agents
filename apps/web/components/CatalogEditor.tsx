"use client";
// Edição do catálogo mestre (COM-001): versão otimista, mídia com texto alternativo, arquivamento e histórico.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type ProductFull, type ProductSummary } from "@/lib/api";

const FIELD: Record<string, string> = {
  title: "título", description: "descrição", category: "categoria", brand: "marca", ncm: "NCM", media: "imagens",
  regulated: "regulado", status: "situação", created: "criação",
};

export default function CatalogEditor({ token, refreshSignal }: { token: string; refreshSignal: number }) {
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [id, setId] = useState("");
  const [p, setP] = useState<ProductFull | null>(null);
  const [form, setForm] = useState({ title: "", description: "", category: "", ncm: "" });
  const [media, setMedia] = useState<{ url: string; alt: string }[]>([]);
  const [hist, setHist] = useState<{ version: number; changed_fields: string[]; at: string }[]>([]);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [conflict, setConflict] = useState(false);

  useEffect(() => { api.products(token).then((r) => setItems(r.items)).catch(() => undefined); }, [token, refreshSignal]);
  const load = useCallback(async (pid: string) => {
    const [full, h] = await Promise.all([api.productFull(token, pid), api.productVersions(token, pid)]);
    setP(full); setHist(h.items); setConflict(false);
    setForm({ title: full.title, description: full.description, category: full.category, ncm: full.ncm ?? "" });
    setMedia(full.media.map((m) => ({ ...m })));
  }, [token]);
  useEffect(() => { if (id) void load(id).catch(() => undefined); }, [id, load]);

  async function save() {
    if (!p) return;
    setMsg(null);
    try {
      const r = await api.updateProduct(token, p.id, {
        expected_version: p.version, ...form, ncm: form.ncm || undefined, media,
      });
      setMsg({ kind: "ok", text: r.changed.length
        ? `Salvo como v${r.version} (${r.changed.map((c) => FIELD[c] ?? c).join(", ")})`
          + (r.listings_marked ? ` · ${r.listings_marked} anúncio(s) precisam ser preparados de novo` : "")
        : "Nada mudou." });
      await load(p.id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "VERSION_CONFLICT") setConflict(true);
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "Falha de conexão com a API" });
    }
  }
  async function toggleArchive() {
    if (!p) return;
    setMsg(null);
    try {
      await api.setProductArchived(token, p.id, p.status !== "ARCHIVED", "alterado pelo escritório");
      await load(p.id);
    } catch (e) { setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "Falha de conexão com a API" }); }
  }
  const move = (i: number, d: -1 | 1) => setMedia((m) => {
    const j = i + d;
    if (j < 0 || j >= m.length) return m;
    return m.map((x, k) => (k === i ? m[j] : k === j ? m[i] : x)) as typeof m;
  });

  return (
    <details className="prepare">
      <summary>Editar catálogo</summary>
      <label>Produto
        <select value={id} onChange={(e) => setId(e.target.value)}>
          <option value="">Selecione…</option>
          {items.map((x) => <option key={x.id} value={x.id}>{x.title}{x.status === "ARCHIVED" ? " (arquivado)" : ""}</option>)}
        </select>
      </label>
      {p && (
        <div className="stack">
          <div className="muted small">v{p.version} · {p.status === "ARCHIVED" ? "Arquivado" : "Ativo"} · SKU {p.sku_base}</div>
          <label>Título<input value={form.title} disabled={p.status === "ARCHIVED"}
            onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          <label>Descrição<textarea rows={3} value={form.description} disabled={p.status === "ARCHIVED"}
            onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <label>Categoria<input value={form.category} disabled={p.status === "ARCHIVED"}
            onChange={(e) => setForm({ ...form, category: e.target.value })} /></label>
          <label>NCM<input inputMode="numeric" maxLength={8} value={form.ncm} disabled={p.status === "ARCHIVED"}
            onChange={(e) => setForm({ ...form, ncm: e.target.value.replace(/\D/g, "") })} /></label>
          <fieldset className="media-list" disabled={p.status === "ARCHIVED"}>
            <legend>Imagens (a primeira é a capa)</legend>
            {media.map((m, i) => (
              <div key={i} className="media-row">
                <input aria-label={`URL da imagem ${i + 1}`} placeholder="https://…/foto.webp" value={m.url}
                  onChange={(e) => setMedia(media.map((x, k) => (k === i ? { ...x, url: e.target.value } : x)))} />
                <input aria-label={`Texto alternativo da imagem ${i + 1}`} placeholder="Descreva a imagem" value={m.alt}
                  onChange={(e) => setMedia(media.map((x, k) => (k === i ? { ...x, alt: e.target.value } : x)))} />
                <button type="button" aria-label={`Subir imagem ${i + 1}`} onClick={() => move(i, -1)}>↑</button>
                <button type="button" aria-label={`Descer imagem ${i + 1}`} onClick={() => move(i, 1)}>↓</button>
                <button type="button" aria-label={`Remover imagem ${i + 1}`}
                  onClick={() => setMedia(media.filter((_, k) => k !== i))}>✕</button>
              </div>
            ))}
            {media.length < 10 && <button type="button" onClick={() => setMedia([...media, { url: "", alt: "" }])}>+ Imagem</button>}
          </fieldset>
          {msg && <p className={msg.kind === "ok" ? "ok small" : "err small"} role={msg.kind === "err" ? "alert" : "status"}>{msg.text}</p>}
          {conflict && <button type="button" onClick={() => load(p.id)}>Recarregar a versão atual (descarta suas mudanças)</button>}
          <div className="row">
            <button type="button" className="primary" disabled={p.status === "ARCHIVED"} onClick={save}>Salvar nova versão</button>
            <button type="button" onClick={toggleArchive}>{p.status === "ARCHIVED" ? "Reativar" : "Arquivar"}</button>
          </div>
          <h4>Histórico</h4>
          <ol className="timeline">{hist.map((h) => (
            <li key={h.version}><span className="muted small">v{h.version} · {new Date(h.at).toLocaleString("pt-BR")}</span>{" "}
              {h.changed_fields.map((c) => FIELD[c] ?? c).join(", ")}</li>))}</ol>
        </div>
      )}
    </details>
  );
}
