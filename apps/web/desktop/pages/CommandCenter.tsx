"use client";
// UX-01 Centro de Comando (UXD-007): vendas, gastos, decisões pendentes e agentes — tudo do backend, com fonte,
// período e marca de simulação. Sem KPI inventado.
import { AlertTriangle, Bot, Building2, CircleDollarSign, Inbox, OctagonX, Plus, ShoppingBag, Sparkles, TrendingUp } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { type AgentTask, type AiUsage, type Alert, api, type Proposal, type SalesDashboard } from "@/lib/api";
import { lookFor, since } from "@/lib/agentIdentity";
import { formatMicroUsd, formatMinor } from "@/lib/format";
import AgentPortrait from "../AgentPortrait";
import { useDesk } from "../context";
import { ConfirmDialog, Empty, Kpi, StateChip } from "../ui";

const ACTIVE = new Set(["PLANNING", "WORKING", "TOOL_CALL", "WAITING_APPROVAL", "BLOCKED", "ERROR"]);
const ACTION: Record<string, string> = {
  PUBLISH_LISTING: "Publicar anúncio", UPDATE_LISTING_MATERIAL: "Atualizar anúncio", CREATE_CAMPAIGN: "Criar campanha",
  INCREASE_BUDGET: "Aumentar orçamento", PAUSE_MEDIA: "Pausar mídia", PAUSE_LISTING: "Pausar anúncio",
  SUPPLIER_PURCHASE: "Comprar no fornecedor", REFUND: "Reembolsar cliente", SPEND: "Despesa", CHANGE_SUPPLIER: "Trocar fornecedor",
  PREPARE_LISTING_DRAFT: "Preparar rascunho de anúncio", RESEARCH: "Pesquisa",
};

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
}

export default function CommandCenter() {
  const desk = useDesk();
  const [sales, setSales] = useState<SalesDashboard | null>(null);
  const [ai, setAi] = useState<AiUsage | null>(null);
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [decisions, setDecisions] = useState<Proposal[] | null>(null);
  const [recent, setRecent] = useState<AgentTask[] | null>(null);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [failed, setFailed] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!desk.me) return; // permissões ainda não chegaram: não decidir "sem acesso" cedo demais
    const t = desk.token;
    const load = <T,>(key: string, p: Promise<T>, set: (v: T) => void) =>
      p.then((v) => { set(v); setFailed((f) => { const { [key]: _, ...rest } = f; return rest; }); })
        .catch((e) => setFailed((f) => ({ ...f, [key]: e instanceof Error ? e.message : String(e) })));
    if (desk.can("dashboard:read")) load("sales", api.salesDashboard(t, 30), setSales);
    if (desk.can("finance:read")) load("ai", api.aiUsage(t), setAi);
    load("alerts", api.alerts(t).then((r) => r.items), setAlerts);
    if (desk.can("approvals:read")) load("decisions", api.approvals(t, "PENDING_APPROVAL").then((r) => r.items), setDecisions);
    load("recent", api.recentTasks(t, 8).then((r) => r.items), setRecent);
  }, [desk.token, desk.me, desk.refresh, desk.approvalsSignal]); // eslint-disable-line react-hooks/exhaustive-deps
  const loading = (key: string) => failed[key]
    ? <p role="alert" style={{ margin: 0, color: "var(--critical)" }}>Não foi possível carregar: {failed[key]}</p>
    : <p className="muted" style={{ margin: 0 }}>Carregando…</p>;

  const agents = useMemo(() => desk.office.order.map((id) => desk.office.agents[id]!).filter(Boolean), [desk.office]);
  const working = agents.filter((a) => ACTIVE.has(a.state));
  const simulated = desk.office.simulated;
  const crit = alerts?.filter((a) => a.severity === "CRITICAL") ?? [];
  const src = simulated ? "simulado" : "realizado";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{greeting()}.</h1>
          <p>Sua operação está em <strong style={{ color: simulated ? "var(--demo-text)" : "var(--success)" }}>{simulated ? "SIMULAÇÃO" : "PRODUÇÃO"}</strong>
            {simulated && " — nenhum dado aqui é venda ou gasto real."}</p>
        </div>
        <div className="actions">
          {desk.can("emergency:stop") && !desk.office.emergencyStop && (
            <button type="button" className="btn danger" onClick={() => setStopping(true)}><OctagonX />Parar novas despesas</button>
          )}
          <button type="button" className="btn" onClick={() => desk.go("office")}><Building2 />Abrir escritório</button>
          {desk.can("ai:missions") && <button type="button" className="btn primary" onClick={() => desk.go("missions")}><Plus />Criar missão</button>}
        </div>
      </div>
      {error && <p role="alert" style={{ color: "var(--critical)" }}>{error}</p>}

      <div className="dk-kpis">
        <Kpi icon={<ShoppingBag size={14} />} label="Pedidos pagos" value={sales ? sales.totals.orders : "—"} sub={`últimos 30 dias · ${src}`} />
        <Kpi icon={<TrendingUp size={14} />} label="Receita líquida" value={sales ? formatMinor(sales.totals.net_revenue_minor) : "—"}
          sub={`30 dias · após tarifas · ${src}`} />
        <Kpi icon={<CircleDollarSign size={14} />} label="Contribuição estimada" value={sales ? formatMinor(sales.totals.contribution_minor) : "—"}
          sub="estimada · antes de mídia e tributos · não é lucro" />
        <Kpi icon={<Sparkles size={14} />} label="IA no mês" value={ai ? formatMicroUsd(ai.month_spend_micro_usd) : "—"}
          sub={ai ? `de ${formatMicroUsd(ai.monthly_budget_micro_usd)} · ${ai.provider === "mock" ? "simulado" : "real"}` : desk.can("finance:read") ? "carregando" : "requer permissão financeira"} />
        <Kpi icon={<AlertTriangle size={14} />} label="Alertas críticos" value={alerts ? crit.length : "—"}
          sub={alerts ? `${alerts.length - crit.length} aviso(s) além destes` : "carregando"} />
      </div>

      <div className="grid-2">
        <div className="dk-stack">
          <section className="dk-card" aria-labelledby="cc-dec">
            <div className="card-title" id="cc-dec"><Inbox size={16} />Decisões pendentes
              {desk.can("approvals:read") && <button type="button" className="dk-link" onClick={() => desk.go("approvals")}>Ver todas</button>}</div>
            {!desk.can("approvals:read") ? <p className="muted" style={{ margin: 0 }}>Seu papel não acompanha aprovações.</p>
              : decisions === null ? loading("decisions")
                : decisions.length === 0 ? <Empty icon={<Inbox />} title="Nada esperando sua decisão">Propostas dos agentes aparecem aqui antes de qualquer gasto ou publicação.</Empty>
                  : <div className="rows">{decisions.slice(0, 3).map((p) => (
                    <div key={p.id} className="row-item">
                      {p.agent_id ? <AgentPortrait agentId={p.agent_id} size={32} /> : <Inbox size={20} aria-hidden="true" />}
                      <div className="grow"><div className="t ellipsis">{ACTION[p.action_type] ?? p.action_type}</div>
                        <div className="meta">{p.agent_id ? desk.office.agents[p.agent_id]?.name ?? p.agent_id : "Proposta manual"} · risco {p.risk_score}
                          {p.monetary_scope_minor > 0 ? ` · ${formatMinor(p.monetary_scope_minor)}` : ""}</div></div>
                      <button type="button" className="btn" onClick={() => desk.go("approvals")}>Revisar proposta</button>
                    </div>))}</div>}
          </section>
          <section className="dk-card" aria-labelledby="cc-alerts">
            <div className="card-title" id="cc-alerts"><AlertTriangle size={16} />Alertas operacionais</div>
            {alerts === null ? loading("alerts")
              : alerts.length === 0 ? <Empty icon={<AlertTriangle />} title="Nenhum alerta aberto" />
                : <div className="rows">{alerts.slice(0, 5).map((a) => (
                  <div key={`${a.kind}${a.ref}`} className="row-item">
                    <span className={`dk-chip ${a.severity === "CRITICAL" ? "crit" : "warn"}`}><AlertTriangle />{a.severity === "CRITICAL" ? "Crítico" : "Aviso"}</span>
                    <div className="grow ellipsis" title={a.message}>{a.message}</div>
                  </div>))}</div>}
          </section>
          <section className="dk-card" aria-labelledby="cc-sales">
            <div className="card-title" id="cc-sales"><TrendingUp size={16} />Receita bruta diária (30 dias)
              <span className="faint" style={{ marginLeft: 8 }}>{src}</span></div>
            {!sales ? (desk.can("dashboard:read") ? loading("sales") : <p className="muted" style={{ margin: 0 }}>Requer permissão de dashboard.</p>)
              : <DailyBars data={sales.daily} />}
          </section>
        </div>
        <div className="dk-stack">
          <section className="dk-card" aria-labelledby="cc-agents">
            <div className="card-title" id="cc-agents"><Bot size={16} />Agentes em atividade
              <button type="button" className="dk-link" onClick={() => desk.go("agents")}>Ver os 13</button></div>
            {working.length === 0 ? (
              <Empty icon={<Bot />} title="Nenhum agente trabalhando agora">Crie uma missão ou peça uma tarefa a um agente.</Empty>
            ) : <div className="rows">{working.slice(0, 5).map((a) => (
              <button key={a.agent_id} type="button" className="row-item" style={{ border: 0, background: "none", textAlign: "left", width: "100%" }}
                onClick={() => desk.openAgent(a.agent_id)}>
                <AgentPortrait agentId={a.agent_id} size={36} />
                <div className="grow"><div className="t">{a.name}</div><div className="meta">{lookFor(a.agent_id).short}</div></div>
                <StateChip state={a.state} />
              </button>))}</div>}
          </section>
          <section className="dk-card" aria-labelledby="cc-recent">
            <div className="card-title" id="cc-recent">Últimas entregas da equipe</div>
            {recent === null ? loading("recent") : recent.length === 0
              ? <Empty icon={<Bot />} title="Ainda não há tarefas" />
              : <div className="rows">{recent.slice(0, 6).map((t) => (
                <button key={t.id} type="button" className="row-item" style={{ border: 0, background: "none", textAlign: "left", width: "100%" }}
                  onClick={() => desk.openAgent(t.agent_id)}>
                  <AgentPortrait agentId={t.agent_id} size={28} />
                  <div className="grow"><div className="ellipsis"><strong>{desk.office.agents[t.agent_id]?.name ?? t.agent_id}</strong> · {desk.taskTitle(t.kind)}</div>
                    <div className="meta">{t.state === "COMPLETED" ? "concluída" : t.state.toLowerCase()} {since(t.created_at)}{t.simulated ? " · simulado" : ""}</div></div>
                </button>))}</div>}
          </section>
          <section className="dk-card" aria-labelledby="cc-office">
            <div className="card-title" id="cc-office"><Building2 size={16} />Escritório</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
              {agents.map((a) => (
                <button key={a.agent_id} type="button" title={`${a.name}`} aria-label={`Abrir ${a.name}`}
                  style={{ border: 0, background: "none", padding: 0 }} onClick={() => desk.openAgent(a.agent_id)}>
                  <AgentPortrait agentId={a.agent_id} size={34} ring={ACTIVE.has(a.state) ? "var(--accent)" : undefined} />
                </button>))}
            </div>
            <button type="button" className="btn" onClick={() => desk.go("office")}><Building2 />Abrir escritório 3D</button>
          </section>
        </div>
      </div>
      {stopping && (
        <ConfirmDialog title="Parar novas despesas?" danger needReason confirmLabel="Acionar kill switch"
          body={<>Trava novas despesas e publicações, revoga delegações e pede a pausa dos anúncios e campanhas.
            Pedidos já contratados continuam. Só um proprietário pode liberar depois.</>}
          onCancel={() => setStopping(false)}
          onConfirm={async (reason) => { await api.emergencyStop(desk.token, reason); setStopping(false); setError(null); }} />
      )}
    </div>
  );
}

function DailyBars({ data }: { data: { date: string; revenue_minor: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.revenue_minor));
  const total = data.reduce((s, d) => s + d.revenue_minor, 0);
  if (total === 0) return <Empty icon={<TrendingUp />} title="Sem vendas no período" />;
  return (
    <div role="img" aria-label={`Receita bruta diária; total ${formatMinor(total)}`}
      style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 96 }}>
      {data.map((d) => (
        <span key={d.date} title={`${new Date(d.date + "T12:00:00Z").toLocaleDateString("pt-BR")}: ${formatMinor(d.revenue_minor)}`}
          style={{ flex: 1, minHeight: 2, height: `${Math.max(2, (d.revenue_minor / max) * 100)}%`, background: d.revenue_minor ? "var(--chart-1)" : "var(--border-soft)",
            borderRadius: "4px 4px 0 0" }} />
      ))}
    </div>
  );
}
