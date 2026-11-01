"use client";
// DataTable (UXD-005, SDD v3 §5 "tabelas com colunas configuráveis, filtro, busca, ordenação, paginação e estados
// vazios explicativos"): ordenação por coluna (aria-sort), colunas visíveis por tabela (lembradas neste computador),
// paginação e linha selecionável. A busca/filtro ficam na página (contexto de cada domínio).
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Columns3 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

export interface Column<T> {
  key: string; label: string; render(row: T): ReactNode; sort?(row: T): string | number; align?: "right";
  /** Pode ser escondida pelo usuário; `hidden` = começa escondida. Colunas sem `optional` estão sempre visíveis. */
  optional?: boolean; hidden?: boolean; minWidth?: number;
}

export function sortRows<T>(rows: T[], col: Column<T> | undefined, dir: 1 | -1): T[] {
  if (!col?.sort) return rows;
  const f = col.sort;
  return [...rows].sort((a, b) => {
    const x = f(a), y = f(b);
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "pt-BR", { numeric: true })) * dir;
  });
}

export function DataTable<T>({ id, columns, rows, rowKey, selected, onSelect, pageSize = 25, label, empty, initialSort }: {
  id: string; columns: Column<T>[]; rows: T[]; rowKey(r: T): string; selected?: string | null; onSelect?(r: T): void;
  pageSize?: number; label: string; empty?: ReactNode; initialSort?: { key: string; dir: 1 | -1 };
}) {
  const storeKey = `aic.cols.${id}`;
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(columns.filter((c) => c.hidden).map((c) => c.key)));
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(initialSort ?? null);
  const [page, setPage] = useState(0);
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    try { const v = localStorage.getItem(storeKey); if (v) setHidden(new Set(JSON.parse(v) as string[])); } catch { /* */ }
  }, [storeKey]);
  useEffect(() => { setPage(0); }, [rows.length, sort]);
  const visible = columns.filter((c) => !hidden.has(c.key));
  const sorted = useMemo(() => sortRows(rows, columns.find((c) => c.key === sort?.key), sort?.dir ?? 1), [rows, sort, columns]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const slice = sorted.slice(page * pageSize, page * pageSize + pageSize);
  const toggle = (k: string) => {
    const n = new Set(hidden); if (n.has(k)) n.delete(k); else n.add(k);
    setHidden(n); try { localStorage.setItem(storeKey, JSON.stringify([...n])); } catch { /* */ }
  };
  if (rows.length === 0 && empty) return <>{empty}</>;
  return (
    <div className="data-table">
      <div className="dt-tools">
        <span className="faint">{rows.length} {rows.length === 1 ? "item" : "itens"}</span>
        {columns.some((c) => c.optional) && (
          <div style={{ position: "relative", marginLeft: "auto" }}>
            <button type="button" className="btn ghost" aria-haspopup="true" aria-expanded={menu} onClick={() => setMenu(!menu)}><Columns3 />Colunas</button>
            {menu && (
              <div className="dk-card dt-menu" role="group" aria-label={`Colunas de ${label}`} onKeyDown={(e) => { if (e.key === "Escape") setMenu(false); }}>
                {columns.filter((c) => c.optional).map((c) => (
                  <label key={c.key} className="inline-field"><input type="checkbox" checked={!hidden.has(c.key)} onChange={() => toggle(c.key)} />{c.label}</label>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <table className="dk-table" aria-label={label}>
        <thead><tr>{visible.map((c) => {
          const active = sort?.key === c.key;
          return (
            <th key={c.key} className={c.align === "right" ? "r" : undefined} style={c.minWidth ? { minWidth: c.minWidth } : undefined}
              aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : c.sort ? "none" : undefined}>
              {c.sort ? (
                <button type="button" className="th-sort" onClick={() => setSort({ key: c.key, dir: active && sort!.dir === 1 ? -1 : 1 })}>
                  {c.label}{active && (sort!.dir === 1 ? <ArrowUp size={12} aria-hidden="true" /> : <ArrowDown size={12} aria-hidden="true" />)}
                </button>
              ) : c.label}
            </th>
          );
        })}</tr></thead>
        <tbody>{slice.map((r) => {
          const k = rowKey(r);
          return (
            <tr key={k} aria-selected={selected === k} className={onSelect ? undefined : "static"} onClick={onSelect ? () => onSelect(r) : undefined}>
              {visible.map((c) => <td key={c.key} className={c.align === "right" ? "r num" : undefined}>{c.render(r)}</td>)}
            </tr>
          );
        })}</tbody>
      </table>
      {pages > 1 && (
        <div className="dt-pager" role="navigation" aria-label={`Páginas de ${label}`}>
          <button type="button" className="icon-btn" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Página anterior"><ChevronLeft size={16} /></button>
          <span className="faint num">{page + 1} de {pages}</span>
          <button type="button" className="icon-btn" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label="Próxima página"><ChevronRight size={16} /></button>
        </div>
      )}
    </div>
  );
}

/** Esqueleto de carregamento (sem números inventados): mantém o layout estável e anuncia "Carregando…". */
export function Skeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="skeleton" role="status" aria-live="polite">
      <span className="sr-only">Carregando…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="sk-row" aria-hidden="true">{Array.from({ length: cols }, (_, j) => <span key={j} className="sk" style={{ width: `${[38, 18, 14, 22, 12][j % 5]}%` }} />)}</div>
      ))}
    </div>
  );
}
