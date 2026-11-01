"use client";
// Gráficos do app desktop (UXD-005/014), seguindo o guia de dataviz: uma série por gráfico (sem eixo duplo),
// colunas finas com ponta arredondada a partir da base, grade recessiva, tooltip por coluna, alternância para
// tabela e estados distintos para "zero real", "sem dados" e "simulado". Texto nunca usa a cor da série.
import { BarChart3, Table2 } from "lucide-react";
import { type ReactNode, useId, useMemo, useState } from "react";

export interface Point { key: string; label: string; value: number }

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v) ?? v;
}
export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (a >= 1e3) return `${(v / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil`;
  return v.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}

/** Colunas (série única). `format` formata valores (ex.: BRL); `axis` formata os ticks (compacto). */
export function ColumnChart({ title, sub, points, format, axis = compact, height = 180, emptyText, simulated, color = "var(--chart-1)" }: {
  title: string; sub?: ReactNode; points: Point[]; format(v: number): string; axis?(v: number): string; height?: number;
  emptyText: string; simulated?: boolean; color?: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const max = useMemo(() => niceMax(Math.max(0, ...points.map((p) => p.value))), [points]);
  const allZero = points.length > 0 && points.every((p) => p.value === 0);
  const W = 640, H = height, L = 48, B = 22, T = 10;
  const band = (W - L) / Math.max(1, points.length);
  const bw = Math.min(24, Math.max(3, band - 2));
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(points.length / 6);
  return (
    <figure className="dk-card chart" aria-labelledby={`${id}-t`}>
      <div className="card-title">
        <span id={`${id}-t`}>{title}</span>
        {simulated && <span className="dk-chip demo">Simulado</span>}
        <button type="button" className="dk-link" onClick={() => setTable(!table)} aria-pressed={table}>
          {table ? <><BarChart3 size={14} />Ver gráfico</> : <><Table2 size={14} />Ver tabela</>}
        </button>
      </div>
      {sub && <p className="faint" style={{ margin: "-6px 0 8px" }}>{sub}</p>}
      {points.length === 0 ? <p className="muted" style={{ margin: 0 }}>{emptyText}</p> : table ? (
        <div style={{ maxHeight: height + 20, overflow: "auto" }}>
          <table className="dk-table"><thead><tr><th>Período</th><th className="r">Valor</th></tr></thead>
            <tbody>{points.map((p) => <tr key={p.key} className="static"><td>{p.label}</td><td className="r num">{format(p.value)}</td></tr>)}</tbody></table>
        </div>
      ) : (
        <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}: ${points.length} pontos; máximo ${format(Math.max(...points.map((p) => p.value)))}`} preserveAspectRatio="none">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="grid" />
                <text x={L - 6} y={y(t) + 4} textAnchor="end" className="tick">{axis(t)}</text>
              </g>
            ))}
            {points.map((p, i) => {
              const x = L + band * i + (band - bw) / 2;
              const h = Math.max(0, y(0) - y(p.value));
              const r = Math.min(4, bw / 2, h);
              return (
                <g key={p.key}>
                  {h > 0 && <path d={`M${x},${y(0)} V${y(0) - h + r} Q${x},${y(0) - h} ${x + r},${y(0) - h} H${x + bw - r} Q${x + bw},${y(0) - h} ${x + bw},${y(0) - h + r} V${y(0)} Z`}
                    fill={color} opacity={hover === null || hover === i ? 1 : 0.45} />}
                  <rect x={L + band * i} y={T} width={band} height={H - T - B + 4} fill="transparent"
                    onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={-1} />
                  {i % labelEvery === 0 && <text x={L + band * i + band / 2} y={H - 6} textAnchor="middle" className="tick">{p.label}</text>}
                </g>
              );
            })}
            <line x1={L} x2={W} y1={y(0)} y2={y(0)} className="base" />
          </svg>
          {hover !== null && points[hover] && (
            <div className="chart-tip" style={{ left: `${((L + band * hover + band / 2) / W) * 100}%` }} role="status">
              <strong className="num">{format(points[hover].value)}</strong><span className="faint">{points[hover].label}</span>
            </div>
          )}
          {allZero && <p className="faint" style={{ margin: "4px 0 0" }}>Valor real zero no período (não é falta de conexão).</p>}
        </div>
      )}
    </figure>
  );
}

/** Medidor horizontal de uso (ex.: teto de orçamento). Faixas de status com texto, nunca só cor. */
export function Meter({ used, total, label, warnAt = 0.8, progress }: { used: number; total: number; label: string; warnAt?: number; progress?: boolean }) {
  const pct = total > 0 ? Math.min(1, used / total) : 0;
  // progress = mais é melhor (checklist); senão é consumo de teto (mais é pior)
  const tone = progress ? "ok" : pct >= 1 ? "crit" : pct >= warnAt ? "warn" : "ok";
  return (
    <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct * 100)} aria-label={label}>
      <span className={`fill ${tone}`} style={{ width: `${Math.max(pct > 0 ? 2 : 0, pct * 100)}%` }} />
    </div>
  );
}
