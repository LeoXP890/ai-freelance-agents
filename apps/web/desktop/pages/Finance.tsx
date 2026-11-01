"use client";
// UX-08 Financeiro (UXD-014): cards enxutos no topo, movimentação e tabelas abaixo. Faturamento, custo e saldo nunca
// se misturam no mesmo número; tudo com fonte e período; "zero real", "sem conexão" e "simulado" são distintos.
// Simulação de repasse só em DRY_RUN e só para quem tem sim:control (o backend recusa fora disso).
import { AlertTriangle, Bot, CircleDollarSign, Landmark, PiggyBank, Receipt, Scale, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { type AiUsage, type Alert, api, ApiError, type BudgetAccount, type Reconciliation, type SalesDashboard } from "@/lib/api";
import { formatBps, formatMicroUsd, formatMinor } from "@/lib/format";
import { composition, NOT_INCLUDED } from "@/lib/financeTerms";
import { ColumnChart, Meter } from "../charts";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { Empty, Kpi } from "../ui";

type Settlement = { id: string; channel: string; external_id: string; gross_minor: number; fees_minor: number; net_minor: number; simulated: boolean; imported_at: string | null };
const CHANNEL: Record<string, string> = { WOOCOMMERCE: "Loja própria (Woo)", MERCADO_LIVRE: "Mercado Livre", SHOPEE: "Shopee", TIKTOK_SHOP: "TikTok Shop" };
export const channelName = (c: string) => CHANNEL[c] ?? c;
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function Finance() {
  const desk = useDesk();
  const [days, setDays] = useState(30);
  const [sales, setSales] = useState<SalesDashboard | null>(null);
  const [accounts, setAccounts] = useState<BudgetAccount[] | null>(null);
  const [rec, setRec] = useState<Reconciliation | null>(null);
  const [settl, setSettl] = useState<Settlement[] | null>(null);
  const [ai, setAi] = useState<AiUsage | null>(null);
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    const t = desk.token;
    const errs: string[] = [];
    const one = <T,>(p: Promise<T>, set: (v: T) => void) => p.then(set).catch((e) => { errs.push(msg(e)); });
    void Promise.all([
      one(api.salesDashboard(t, days), setSales),
      one(api.finance(t), (r) => setAccounts(r.accounts as unknown as BudgetAccount[])),
      one(api.reconciliation(t), setRec),
      one(api.settlements(t), (r) => setSettl(r.items)),
      one(api.aiUsage(t), setAi),
      one(api.alerts(t), (r) => setAlerts(r.items)),
    ]).then(() => setFailed(errs.length ? errs[0]! : null));
  }, [desk.token, days]);
  // espera /auth/me: sem isso a permissão parece ausente no primeiro render e nada carrega
  useEffect(() => { if (desk.me && desk.can("finance:read")) load(); }, [load, desk.refresh, desk.me]); // eslint-disable-line react-hooks/exhaustive-deps

  const sim = desk.office.simulated;
  const src = sim ? "simulado" : "realizado";
  const avail = accounts?.reduce((s, a) => s + a.available_minor, 0) ?? 0;
  const reserved = accounts?.reduce((s, a) => s + a.outstanding_reservations_minor, 0) ?? 0;
  const spent = accounts?.reduce((s, a) => s + a.reconciled_spend_minor, 0) ?? 0;
  const points = useMemo(() => (sales?.daily ?? []).map((d) => ({ key: d.date, value: d.revenue_minor,
    label: new Date(`${d.date}T12:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) })), [sales]);
  const aiByAgent = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of ai?.by_agent_model ?? []) m.set(r.agent_id, (m.get(r.agent_id) ?? 0) + r.cost_micro_usd);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [ai]);

  if (desk.me && !desk.can("finance:read")) return (
    <div className="page"><div className="page-head"><div><h1>Financeiro</h1></div></div>
      <Empty icon={<Landmark />} title="Requer permissão financeira">Peça ao proprietário o papel Financeiro.</Empty></div>
  );
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Financeiro</h1>
          <p>Receita, custos, orçamento aprovado e conciliação de repasses — {sim ? <strong style={{ color: "var(--demo-text)" }}>valores simulados</strong> : "valores realizados"}.</p>
        </div>
        <div className="actions">
          <div className="dk-seg" role="group" aria-label="Período">
            {[7, 30, 90].map((d) => <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>{d} dias</button>)}
          </div>
        </div>
      </div>
      {failed && <div className="callout crit" role="alert" style={{ marginBottom: 12 }}>Sem conexão com parte dos dados financeiros: {failed}. Os números abaixo podem estar incompletos.</div>}

      <div className="dk-kpis">
        <Kpi icon={<TrendingUp size={14} />} label="Receita líquida" value={sales ? formatMinor(sales.totals.net_revenue_minor) : "—"} sub={`${days} dias · após tarifas e reembolsos · ${src}`} />
        <Kpi icon={<Scale size={14} />} label="Contribuição" value={sales ? formatMinor(sales.totals.contribution_minor) : "—"} sub="líquida − custo do fornecedor · não é lucro nem caixa" />
        <Kpi icon={<PiggyBank size={14} />} label="Saldo disponível" value={accounts ? formatMinor(avail) : "—"} sub={`teto aprovado − gasto − reservas · ${accounts?.length ?? 0} contas`} />
        <Kpi icon={<Receipt size={14} />} label="Reservado agora" value={accounts ? formatMinor(reserved) : "—"} sub={`gasto conciliado ${formatMinor(spent)}`} />
        <Kpi icon={<Bot size={14} />} label="IA no mês" value={ai ? formatMicroUsd(ai.month_spend_micro_usd) : "—"}
          sub={ai ? `de ${formatMicroUsd(ai.monthly_budget_micro_usd)} · provedor ${ai.provider}` : "custo de modelos"} />
      </div>

      <div className="grid-2">
          <ColumnChart title="Receita bruta por dia" sub={`Pedidos pagos · ${days} dias · BRL · ${src}`} points={points}
            format={(v) => formatMinor(v)} axis={(v) => `R$ ${(v / 100).toLocaleString("pt-BR", { notation: "compact" })}`}
            emptyText="Sem dados de vendas para o período." simulated={sim} />
          <section className="dk-card" aria-label="Orçamento aprovado">
            <div className="card-title"><PiggyBank size={16} />Orçamento aprovado</div>
            {!accounts ? <p className="muted" style={{ margin: 0 }}>Carregando…</p> : accounts.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma conta de orçamento.</p> : (
              <div className="rows">{accounts.map((a) => {
                const used = a.reconciled_spend_minor + a.outstanding_reservations_minor + a.safety_hold_minor;
                return (
                  <div key={a.code} className="budget">
                    <div className="row-head"><strong>{a.name}</strong>{a.simulated && <span className="dk-chip demo">Simulado</span>}<span className="num" style={{ marginLeft: "auto" }}>{formatMinor(a.available_minor)} <span className="faint">disponível</span></span></div>
                    <Meter used={used} total={a.approved_cap_minor} label={`Uso do teto de ${a.name}`} />
                    <div className="faint num">teto {formatMinor(a.approved_cap_minor)} · gasto {formatMinor(a.reconciled_spend_minor)} · reservado {formatMinor(a.outstanding_reservations_minor)} · reserva de segurança {formatMinor(a.safety_hold_minor)}</div>
                  </div>
                );
              })}</div>
            )}
            <p className="faint" style={{ margin: "8px 0 0" }}>Gastos só acontecem com reserva atômica nesta conta, depois de aprovação.</p>
          </section>
      </div>
      <div style={{ margin: "16px 0" }}>
          <section className="dk-card table-card" aria-label="Margem por canal">
            <div className="card-title" style={{ padding: "10px 10px 0" }}><CircleDollarSign size={16} />Margem por canal <span className="faint">{days} dias</span></div>
            {!sales ? <Skeleton /> : sales.channels.length === 0 ? <Empty icon={<CircleDollarSign />} title="Nenhuma venda no período">Valor real zero, não é falha de conexão.</Empty> : (
              <table className="dk-table">
                <thead><tr><th>Canal</th><th className="r">Pedidos</th><th className="r">Receita</th><th className="r">Tarifas</th><th className="r">Custo</th><th className="r">Reembolsos</th><th className="r">Contribuição</th><th className="r" title="contribuição ÷ receita bruta">Margem de contribuição</th><th className="r">Cancel.</th></tr></thead>
                <tbody>{sales.channels.map((c) => (
                  <tr key={c.channel} className="static">
                    <td><strong>{channelName(c.channel)}</strong></td>
                    <td className="r num">{c.orders}</td>
                    <td className="r num">{formatMinor(c.revenue_minor)}</td>
                    <td className="r num">{c.fees_minor !== undefined ? formatMinor(c.fees_minor) : "—"}</td>
                    <td className="r num">{c.cost_known === false ? <span className="faint">desconhecido</span> : c.cost_minor !== undefined ? formatMinor(c.cost_minor) : "—"}</td>
                    <td className="r num">{c.refund_minor ? formatMinor(c.refund_minor) : "—"}</td>
                    <td className="r num" style={{ color: c.contribution_minor < 0 ? "var(--critical)" : undefined }}>{formatMinor(c.contribution_minor)}</td>
                    <td className="r num">{formatBps(c.margin_bps)}</td>
                    <td className="r num">{formatBps(c.cancel_rate_bps)}</td>
                  </tr>))}</tbody>
              </table>
            )}
            {sales?.note && <p className="faint" style={{ padding: "0 10px 8px", margin: 0 }}>{sales.note}</p>}
          </section>
          {sales && sales.channels.length > 0 && (
            <section className="dk-card" aria-label="Como os números são calculados" style={{ marginTop: 16 }}>
              <div className="card-title"><Scale size={16} />Como os números são calculados <span className="faint">{days} dias · todos os canais</span></div>
              <dl className="composition">{composition(sales.channels).map((r) => (
                <div key={r.key} className={r.op === "=" ? "total" : ""}>
                  <dt><span className="op" aria-hidden="true">{r.op}</span>{r.label}<span className="faint"> — {r.hint}</span></dt>
                  <dd className="num" style={{ color: r.op === "=" && r.value < 0 ? "var(--critical)" : undefined }}>{formatMinor(r.value)}</dd>
                </div>))}</dl>
              <p className="faint" style={{ margin: "8px 0 0" }}>
                Contribuição <strong>não é lucro líquido nem fluxo de caixa</strong>. Fica de fora: {NOT_INCLUDED.join(", ")}.
              </p>
            </section>
          )}
      </div>
      <div className="grid-2">
        <div className="dk-stack">
          <section className="dk-card" aria-label="Conciliação de repasses">
            <div className="card-title"><Scale size={16} />Conciliação de repasses
              {sim && desk.can("sim:control") && <button type="button" className="dk-link" disabled={busy || !desk.online} onClick={async () => {
                setBusy(true);
                try { await api.simSettlement(desk.token, "WOOCOMMERCE"); desk.notify("Repasse simulado importado e conciliado."); load(); }
                catch (e) { desk.notify(msg(e), "crit"); } finally { setBusy(false); }
              }}>Simular repasse (Woo)</button>}
            </div>
            {!rec ? <p className="muted" style={{ margin: 0 }}>Carregando…</p> : (
              <>
                <div className="facts four">
                  <div><span className="faint">Conciliados</span><strong className="num">{rec.totals.matched}</strong></div>
                  <div><span className="faint">Divergentes</span><strong className="num" style={{ color: rec.totals.divergent ? "var(--critical)" : undefined }}>{rec.totals.divergent}</strong>
                    {rec.totals.divergent > 0 && <span className="faint num">{formatMinor(rec.totals.divergence_minor)}</span>}</div>
                  <div><span className="faint">A receber</span><strong className="num">{formatMinor(rec.totals.receivable_open_minor)}</strong></div>
                  <div><span className="faint">Em disputa</span><strong className="num">{rec.totals.disputes ?? 0}</strong>
                    {!!rec.totals.at_risk_minor && <span className="faint num">{formatMinor(rec.totals.at_risk_minor)} em risco</span>}</div>
                </div>
                {rec.divergent.length > 0 && (
                  <table className="dk-table" style={{ marginTop: 10 }}>
                    <thead><tr><th>Pedido</th><th>Canal</th><th className="r">Esperado</th><th className="r">Repassado</th><th className="r">Diferença</th></tr></thead>
                    <tbody>{rec.divergent.map((r) => (
                      <tr key={r.order_id} className="static"><td>{r.order_external_id}{r.note && <div className="faint">{r.note}</div>}</td><td>{channelName(r.channel)}</td>
                        <td className="r num">{formatMinor(r.expected_net_minor)}</td><td className="r num">{formatMinor(r.settled_net_minor)}</td>
                        <td className="r num" style={{ color: "var(--critical)" }}>{formatMinor(r.diff_minor)}</td></tr>))}</tbody>
                  </table>
                )}
                <p className="faint" style={{ margin: "8px 0 0" }}>{rec.note}</p>
              </>
            )}
          </section>
          <section className="dk-card table-card" aria-label="Repasses recebidos">
            <div className="card-title" style={{ padding: "10px 10px 0" }}><Landmark size={16} />Repasses recebidos</div>
            {!settl ? <Skeleton /> : settl.length === 0 ? <p className="muted" style={{ padding: "0 10px 10px", margin: 0 }}>Nenhum repasse importado ainda.</p> : (
              <table className="dk-table">
                <thead><tr><th>Data</th><th>Canal</th><th className="r">Bruto</th><th className="r">Tarifas</th><th className="r">Líquido</th></tr></thead>
                <tbody>{settl.slice(0, 12).map((s) => (
                  <tr key={s.id} className="static"><td className="faint">{s.imported_at ? new Date(s.imported_at).toLocaleDateString("pt-BR") : "—"}{s.simulated ? " · simulado" : ""}</td>
                    <td>{channelName(s.channel)}</td><td className="r num">{formatMinor(s.gross_minor)}</td><td className="r num">{formatMinor(s.fees_minor)}</td><td className="r num">{formatMinor(s.net_minor)}</td></tr>))}</tbody>
              </table>
            )}
          </section>
        </div>
        <div className="dk-stack">
          <section className="dk-card" aria-label="Custo de IA por agente">
            <div className="card-title"><Bot size={16} />Custo de IA por agente <span className="faint">mês corrente · US$</span></div>
            {!ai ? <p className="muted" style={{ margin: 0 }}>Carregando…</p> : aiByAgent.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma chamada de modelo no mês.</p> : (
              <div className="hbars">{aiByAgent.map(([id, v]) => (
                <div key={id} className="hbar">
                  <span className="who">{desk.office.agents[id]?.name ?? id}</span>
                  <span className="track"><span style={{ width: `${Math.max(2, (v / aiByAgent[0]![1]) * 100)}%` }} /></span>
                  <span className="num faint">{formatMicroUsd(v)}</span>
                </div>))}</div>
            )}
          </section>
          <section className="dk-card" aria-label="Alertas financeiros e operacionais">
            <div className="card-title"><AlertTriangle size={16} />Alertas {alerts && <span className="faint">({alerts.length})</span>}</div>
            {!alerts ? <p className="muted" style={{ margin: 0 }}>Carregando…</p> : alerts.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhum alerta agora.</p> : (
              <ul className="alert-list">{alerts.slice(0, 10).map((a, i) => (
                <li key={i} className={a.severity === "CRITICAL" ? "crit" : "warn"}><span className={`dk-chip ${a.severity === "CRITICAL" ? "crit" : "warn"}`}><AlertTriangle aria-hidden="true" />{a.severity === "CRITICAL" ? "Crítico" : "Atenção"}</span>{a.message}</li>))}</ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
