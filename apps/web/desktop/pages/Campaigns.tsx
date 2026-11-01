"use client";
// UX-07 Campanhas (UXD-013): mídia paga com teto aprovado, gasto conciliado com o ledger e métricas por dia.
// Receita aqui é ATRIBUÍDA pela plataforma (não é venda conciliada) e aparece separada do gasto. Pausar = proposta.
import { AlertTriangle, CalendarPlus, CheckCircle2, Megaphone, PauseCircle, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type CampaignRow } from "@/lib/api";
import { formatBps, formatMinor } from "@/lib/format";
import { ColumnChart, Meter } from "../charts";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { ConfirmDialog, Empty, Kpi } from "../ui";

export const CAMPAIGN_STATUS: Record<string, { label: string; tone: string }> = {
  ACTIVE: { label: "Ativa", tone: "ok" }, PAUSE_REQUESTED: { label: "Pausa solicitada", tone: "warn" }, PAUSED: { label: "Pausada", tone: "" },
  ENDED: { label: "Teto atingido", tone: "warn" }, ERROR: { label: "Pausa falhou", tone: "crit" },
};
export const roas = (x: number | null) => (x === null ? "—" : `${(x / 100).toFixed(2).replace(".", ",")}×`);
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export function useCampaigns() {
  const desk = useDesk();
  const [items, setItems] = useState<CampaignRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api.campaigns(desk.token).then((r) => { setItems(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [desk.token]);
  useEffect(() => { if (desk.me && desk.can("finance:read")) load(); }, [load, desk.refresh, desk.me]); // eslint-disable-line react-hooks/exhaustive-deps
  return { items, error, load };
}

export default function Campaigns() {
  const desk = useDesk();
  const { items, error, load } = useCampaigns();
  const [selected, setSelected] = useState<string | null>(null);
  const t = useMemo(() => {
    const xs = items ?? [];
    const sum = (f: (c: CampaignRow) => number) => xs.reduce((s, c) => s + f(c), 0);
    return { spend: sum((c) => c.spend_minor), cap: sum((c) => c.cap_minor), imp: sum((c) => c.impressions), clk: sum((c) => c.clicks),
      conv: sum((c) => c.conversions), rev: sum((c) => c.revenue_minor), active: xs.filter((c) => c.status === "ACTIVE").length, unrec: xs.filter((c) => !c.reconciled).length };
  }, [items]);
  const sel = items?.find((c) => c.id === selected) ?? null;
  if (desk.me && !desk.can("finance:read")) return <div className="page"><Empty icon={<Megaphone />} title="Requer permissão financeira" /></div>;
  return (
    <div className="page split-page">
      <div className="page-head"><div><h1>Campanhas</h1><p>Mídia paga: teto aprovado, gasto conciliado com o ledger, desempenho e pausas — campanhas novas só nascem de proposta aprovada.</p></div></div>
      <div className="dk-kpis">
        <Kpi label="Gasto em mídia" value={items ? formatMinor(t.spend) : "—"} sub={`de ${formatMinor(t.cap)} de teto aprovado`} />
        <Kpi label="Campanhas ativas" value={items ? t.active : "—"} sub={t.unrec ? `${t.unrec} divergente(s) do ledger` : "gasto conciliado com o ledger"} />
        <Kpi label="Cliques · CTR" value={items ? t.clk.toLocaleString("pt-BR") : "—"} sub={t.imp ? `CTR ${formatBps(Math.floor((t.clk * 10_000) / t.imp))} · ${t.imp.toLocaleString("pt-BR")} impressões` : "sem impressões"} />
        <Kpi label="Conversões · CPA" value={items ? t.conv : "—"} sub={t.conv ? `CPA ${formatMinor(Math.floor(t.spend / t.conv))}` : "sem conversões"} />
        <Kpi label="ROAS atribuído" value={t.spend ? roas(Math.floor((t.rev * 100) / t.spend)) : "—"} sub="receita atribuída pela plataforma ÷ gasto" />
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      <div className={`split ${sel ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Campanhas">
          {items === null ? <Skeleton /> : items.length === 0 ? (
            <Empty icon={<Megaphone />} title="Nenhuma campanha criada">Campanhas nascem de uma proposta do Pixel aprovada na Central de Aprovações.</Empty>
          ) : (
            <table className="dk-table">
              <thead><tr><th>Campanha</th><th>Situação</th><th style={{ minWidth: 160 }}>Gasto / teto</th><th className="r opt">CTR</th><th className="r opt">CPC</th><th className="r">ROAS</th><th>Ledger</th></tr></thead>
              <tbody>{items.map((c) => { const st = CAMPAIGN_STATUS[c.status] ?? { label: c.status, tone: "" }; return (
                <tr key={c.id} aria-selected={selected === c.id} onClick={() => setSelected(c.id)}>
                  <td><button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setSelected(c.id); }} aria-label={`Abrir campanha ${c.name}`}>{c.name}</button>
                    <div className="faint">{c.provider}{c.simulated ? " · simulada" : ""}</div></td>
                  <td><span className={`dk-chip ${st.tone}`}>{st.label}</span></td>
                  <td><span className="num">{formatMinor(c.spend_minor)}</span> <span className="faint num">/ {formatMinor(c.cap_minor)}</span><Meter used={c.spend_minor} total={c.cap_minor} label={`Teto usado de ${c.name}`} /></td>
                  <td className="r num opt">{c.ctr_bps === null ? "—" : formatBps(c.ctr_bps)}</td>
                  <td className="r num opt">{c.cpc_minor === null ? "—" : formatMinor(c.cpc_minor)}</td>
                  <td className="r num">{roas(c.roas_x100)}</td>
                  <td>{c.reconciled ? <span className="dk-chip ok"><CheckCircle2 aria-hidden="true" />conciliado</span> : <span className="dk-chip crit"><AlertTriangle aria-hidden="true" />divergente</span>}</td>
                </tr>); })}</tbody>
            </table>
          )}
        </section>
        {sel && <CampaignPane c={sel} onClose={() => setSelected(null)} onChanged={load} />}
      </div>
    </div>
  );
}

function CampaignPane({ c, onClose, onChanged }: { c: CampaignRow; onClose(): void; onChanged(): void }) {
  const desk = useDesk();
  const [pausing, setPausing] = useState(false);
  const [busy, setBusy] = useState(false);
  const st = CAMPAIGN_STATUS[c.status] ?? { label: c.status, tone: "" };
  const pts = c.days.map((d) => ({ key: d.day, value: d.spend_minor, label: new Date(`${d.day}T12:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) }));
  return (
    <aside className="dk-card detail-pane" aria-label="Detalhe da campanha">
      <div className="detail-head"><div style={{ minWidth: 0 }}><div className="faint">{c.provider} · {c.external_id ?? "sem ID externo"}{c.simulated ? " · simulada" : ""}</div><h2 className="detail-title">{c.name}</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar campanha"><X size={18} /></button></div>
      <div className="detail-body">
        <div className="chip-row"><span className={`dk-chip ${st.tone}`}>{st.label}</span>{c.reconciled ? <span className="dk-chip ok">gasto conciliado com o ledger</span> : <span className="dk-chip crit">ledger {formatMinor(c.ledger_spend_minor)} ≠ plataforma {formatMinor(c.spend_minor)}</span>}</div>
        <div className="facts">
          <div><span className="faint">Gasto</span><strong className="num">{formatMinor(c.spend_minor)}</strong><span className="faint num">{formatBps(c.cap_used_bps)} do teto {formatMinor(c.cap_minor)}</span></div>
          <div><span className="faint">Orçamento diário</span><strong className="num">{formatMinor(c.daily_budget_minor)}</strong></div>
          <div><span className="faint">Receita atribuída</span><strong className="num">{formatMinor(c.revenue_minor)}</strong><span className="faint">pela plataforma, não conciliada</span></div>
          <div><span className="faint">ROAS · conversões</span><strong className="num">{roas(c.roas_x100)} · {c.conversions}</strong></div>
        </div>
        <ColumnChart title="Gasto por dia" sub="BRL · importado da plataforma de mídia" points={pts} format={(v) => formatMinor(v)} height={150}
          axis={(v) => `R$ ${(v / 100).toLocaleString("pt-BR", { notation: "compact" })}`} emptyText="Nenhum dia de métricas importado ainda." simulated={c.simulated} />
      </div>
      {(desk.can("proposals:create") || (desk.can("sim:control") && desk.office.simulated)) && (
        <div className="detail-foot"><div className="decision-bar">
          {desk.can("sim:control") && desk.office.simulated && ["ACTIVE", "PAUSE_REQUESTED", "ERROR"].includes(c.status) && (
            <button type="button" className="btn" disabled={busy || !desk.online} onClick={async () => {
              setBusy(true); try { await api.simCampaignDays(desk.token, c.id, 1); desk.notify("1 dia de métricas simulado importado."); onChanged(); } catch (e) { desk.notify(msg(e), "crit"); } finally { setBusy(false); }
            }}><CalendarPlus />Simular 1 dia</button>)}
          {desk.can("proposals:create") && ["ACTIVE", "ERROR"].includes(c.status) && <button type="button" className="btn" disabled={!desk.online} onClick={() => setPausing(true)}><PauseCircle />Propor pausa</button>}
        </div></div>
      )}
      {pausing && <ConfirmDialog title={`Propor pausa de “${c.name}”?`} needReason confirmLabel="Propor pausa" body="A pausa vira proposta na Central de Aprovações; o gasto continua até a execução confirmar."
        onCancel={() => setPausing(false)} onConfirm={async (r) => { await api.pauseCampaign(desk.token, c.id, r); setPausing(false); desk.notify("Pausa proposta — aguarda aprovação."); onChanged(); }} />}
    </aside>
  );
}
