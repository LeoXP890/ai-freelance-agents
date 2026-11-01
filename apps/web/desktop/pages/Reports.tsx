"use client";
// Relatórios (UXD-014): vendas por canal (receita líquida) e atribuição de mídia (ROAS por campanha) — um gráfico por
// medida, sem eixo duplo; cada um com tabela alternativa. Receita atribuída nunca é somada à receita conciliada.
import { BarChart3, Table2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type SalesDashboard } from "@/lib/api";
import { formatBps, formatMinor } from "@/lib/format";
import { ColumnChart } from "../charts";
import { useDesk } from "../context";
import { Empty, Kpi } from "../ui";
import { roas, useCampaigns } from "./Campaigns";
import { channelName } from "./Finance";

function HBars({ title, sub, rows, value, empty }: { title: string; sub: string; rows: { key: string; label: string; v: number; text: string }[]; value: string; empty: string }) {
  const [table, setTable] = useState(false);
  const max = Math.max(0, ...rows.map((r) => r.v));
  return (
    <section className="dk-card" aria-label={title}>
      <div className="card-title">{title}{rows.length > 0 && <button type="button" className="dk-link" onClick={() => setTable(!table)} aria-pressed={table}>{table ? <><BarChart3 size={14} />Ver gráfico</> : <><Table2 size={14} />Ver tabela</>}</button>}</div>
      <p className="faint" style={{ margin: "-6px 0 10px" }}>{sub}</p>
      {rows.length === 0 ? <p className="muted" style={{ margin: 0 }}>{empty}</p> : table ? (
        <table className="dk-table"><thead><tr><th>Item</th><th className="r">{value}</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.key} className="static"><td>{r.label}</td><td className="r num">{r.text}</td></tr>)}</tbody></table>
      ) : (
        <div className="hbars">{rows.map((r) => (
          <div key={r.key} className="hbar wide" title={`${r.label}: ${r.text}`}><span className="who ellipsis">{r.label}</span>
            <span className="track"><span style={{ width: `${max > 0 ? Math.max(r.v > 0 ? 2 : 0, (r.v / max) * 100) : 0}%` }} /></span><span className="num">{r.text}</span></div>))}</div>
      )}
    </section>
  );
}

export default function Reports() {
  const desk = useDesk();
  const [days, setDays] = useState(30);
  const [d, setD] = useState<SalesDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { items } = useCampaigns();
  useEffect(() => { if (desk.me && desk.can("dashboard:read")) api.salesDashboard(desk.token, days).then(setD).catch((e) => setError(String(e))); }, [desk.token, days, desk.refresh, desk.me]); // eslint-disable-line react-hooks/exhaustive-deps
  const pts = useMemo(() => (d?.daily ?? []).map((x) => ({ key: x.date, value: x.revenue_minor, label: new Date(`${x.date}T12:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) })), [d]);
  const spend = (items ?? []).filter((c) => c.spend_minor > 0);
  if (desk.me && !desk.can("dashboard:read") && !desk.can("finance:read")) return <div className="page"><Empty icon={<BarChart3 />} title="Seu papel não acessa relatórios" /></div>;
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Relatórios</h1><p>Vendas por canal e atribuição de mídia{d?.simulated ? " — dados simulados" : ""}.</p></div>
        <div className="actions"><div className="dk-seg" role="group" aria-label="Período">{[7, 30, 90].map((x) => <button key={x} type="button" aria-pressed={days === x} onClick={() => setDays(x)}>{x} dias</button>)}</div></div>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      {d && (
        <div className="dk-kpis">
          <Kpi label="Pedidos" value={d.totals.orders} sub={`${days} dias`} />
          <Kpi label="Receita líquida" value={formatMinor(d.totals.net_revenue_minor)} sub="após tarifas e reembolsos" />
          <Kpi label="Contribuição estimada" value={formatMinor(d.totals.contribution_minor)} sub="sem mídia nem tributos" />
          <Kpi label="Canais com venda" value={d.channels.length} sub="no período" />
          <Kpi label="Ticket médio" value={d.totals.orders ? formatMinor(Math.round(d.channels.reduce((s, c) => s + c.revenue_minor, 0) / d.totals.orders)) : "—"} sub="receita bruta ÷ pedidos" />
        </div>
      )}
      <div className="grid-2">
        <div className="dk-stack">
          <ColumnChart title="Receita bruta diária" sub={`inclui pedidos depois reembolsados · ${days} dias · BRL`} points={pts} format={(v) => formatMinor(v)}
            axis={(v) => `R$ ${(v / 100).toLocaleString("pt-BR", { notation: "compact" })}`} emptyText="Sem dados de vendas." simulated={d?.simulated} />
          <HBars title="Receita líquida por canal" sub={`${days} dias · após tarifas e reembolsos`} value="Receita líquida" empty="Sem vendas no período."
            rows={(d?.channels ?? []).map((c) => ({ key: c.channel, label: `${channelName(c.channel)} · ${c.orders} ped. · margem ${formatBps(c.margin_bps)}`, v: Math.max(0, c.net_revenue_minor), text: formatMinor(c.net_revenue_minor) }))} />
        </div>
        <div className="dk-stack">
          <HBars title="ROAS por campanha" sub="receita atribuída pela plataforma ÷ gasto — não é receita conciliada" value="ROAS" empty="Sem gasto de mídia importado ainda."
            rows={spend.map((c) => ({ key: c.id, label: c.name, v: c.roas_x100 ?? 0, text: roas(c.roas_x100) }))} />
          <HBars title="Gasto de mídia por campanha" sub="BRL · conciliado com o ledger quando marcado" value="Gasto" empty="Sem gasto de mídia."
            rows={spend.map((c) => ({ key: c.id, label: `${c.name}${c.reconciled ? "" : " (divergente)"}`, v: c.spend_minor, text: formatMinor(c.spend_minor) }))} />
          {d?.note && <p className="faint" style={{ margin: 0 }}>{d.note}</p>}
        </div>
      </div>
    </div>
  );
}
