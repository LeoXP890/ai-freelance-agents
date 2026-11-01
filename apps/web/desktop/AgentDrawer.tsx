"use client";
// Agent Workspace (UXD-009): visão geral, tarefas, conversas e ferramentas/custos — dados reais do backend.
import { BellRing, Box, MapPin, MessageSquare, Wrench, X } from "lucide-react";
import { useEffect, useState } from "react";
import { type AgentTask, type AiSpec, type AiUsage, api, type Proposal } from "@/lib/api";
import { ACTION_LABEL, formatMinor } from "@/lib/format";
import { lookFor, since, STATE_UI } from "@/lib/agentIdentity";
import { formatMicroUsd } from "@/lib/format";
import { AgentSkills } from "@/components/AiPanels";
import AgentPortrait from "./AgentPortrait";
import AgentRoutines from "./AgentRoutines";
import { taskSummary, useDesk } from "./context";
import { StateChip } from "./ui";

type Tab = "overview" | "tasks" | "routines" | "chat" | "tools";
const TASK_STATE: Record<string, string> = {
  CREATED: "Criada", QUEUED: "Na fila", PLANNING: "Planejando", WORKING: "Em andamento", TOOL_CALL: "Usando ferramenta",
  WAITING_APPROVAL: "Aguardando aprovação", BLOCKED: "Impedida", COMPLETED: "Concluída", FAILED: "Falhou", CANCELLED: "Cancelada",
  ERROR: "Com erro",
};

export default function AgentDrawer({ agentId, onClose, onShowInOffice }: {
  agentId: string; onClose(): void; onShowInOffice?(id: string): void;
}) {
  const desk = useDesk();
  const a = desk.office.agents[agentId];
  const look = lookFor(agentId);
  const [tab, setTab] = useState<Tab>("overview");
  const [tasks, setTasks] = useState<AgentTask[] | null>(null);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [skills, setSkills] = useState<AiSpec[] | null>(null);
  const [waiting, setWaiting] = useState<Proposal[] | null>(null);
  const lastSeq = a?.last_seq ?? 0;

  useEffect(() => {
    let alive = true;
    api.aiSpecs(desk.token).then((r) => alive && setSkills(r.items.filter((s) => s.agent_id === agentId))).catch(() => alive && setSkills([]));
    return () => { alive = false; };
  }, [agentId, desk.token]);
  const canApprovals = desk.can("approvals:read"), canFinance = desk.can("finance:read");
  useEffect(() => {  // o que precisa do proprietário: propostas deste agente aguardando decisão
    if (!canApprovals) { setWaiting([]); return; }
    let alive = true;
    api.approvals(desk.token, "PENDING_APPROVAL").then((r) => alive && setWaiting(r.items.filter((p) => p.agent_id === agentId)))
      .catch(() => alive && setWaiting(null));
    return () => { alive = false; };
  }, [agentId, desk.token, canApprovals, desk.approvalsSignal, lastSeq]);  // não a cada evento do escritório

  useEffect(() => {
    let alive = true;
    api.tasks(desk.token, agentId).then((r) => alive && setTasks(r.items)).catch(() => alive && setTasks([]));
    return () => { alive = false; };
  }, [agentId, desk.token, lastSeq]);
  useEffect(() => {
    if (tab !== "tools" || !canFinance) return;
    api.aiUsage(desk.token).then(setUsage).catch(() => undefined);
  }, [tab, desk.token, canFinance]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!a) return null;
  const st = STATE_UI[a.state] ?? STATE_UI.IDLE!;
  const current = a.task_id ? tasks?.find((t) => t.id === a.task_id) : undefined;
  const active = ["PLANNING", "WORKING", "TOOL_CALL", "WAITING_APPROVAL", "BLOCKED"].includes(a.state);
  const mine = usage?.by_agent_model.filter((u) => u.agent_id === agentId) ?? [];

  return (
    <aside className="drawer" aria-label={`Agente ${a.name}`}>
      <div className="drawer-head">
        <AgentPortrait agentId={agentId} size={64} />
        <div>
          <h2 style={{ fontSize: 20 }}>{a.name}</h2>
          <div className="muted">{look.short}</div>
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <StateChip state={a.state} />
            <span className="dk-chip"><MapPin />{look.department}</span>
          </div>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar agente"><X /></button>
      </div>
      <div className="tabs" role="tablist" aria-label="Seções do agente">
        {([["overview", "Visão geral"], ["tasks", "Tarefas"], ["routines", "Rotinas"], ["chat", "Conversas"], ["tools", "Ferramentas e custos"]] as [Tab, string][]).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      <div className="drawer-body">
        {tab === "overview" && (
          <>
            <div className="dk-card">
              <div className="card-title">Agora</div>
              <p style={{ margin: 0 }}>
                {active ? <><strong>{st.label}</strong>{current ? <> em <strong>{desk.taskTitle(current.kind)}</strong></> : null}</>
                  : a.state === "COMPLETED" ? <>Concluiu a última tarefa {since(a.last_event_at)}. Sem trabalho em andamento.</>
                    : a.state === "ERROR" ? <>A última tarefa terminou com erro {since(a.last_event_at)}.</>
                      : <>Disponível. Sem trabalho em andamento.</>}
              </p>
              <p className="faint" style={{ margin: "6px 0 0" }}>Atualizado {since(a.last_event_at)} · fonte: presença do agente (backend)</p>
            </div>
            <div className="dk-card" aria-label="Precisa de você">
              <div className="card-title"><BellRing size={15} aria-hidden="true" /> Precisa de você</div>
              {waiting === null ? <p className="muted" style={{ margin: 0 }}>Não foi possível consultar as aprovações.</p>
                : waiting.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nada aguardando sua decisão deste agente.</p>
                  : <div className="rows">{waiting.slice(0, 4).map((p) => (
                    <button key={p.id} type="button" className="row-item" style={{ border: 0, background: "none", textAlign: "left", width: "100%" }}
                      onClick={() => desk.openApproval(p.id)}>
                      <span className="grow"><span className="t ellipsis" style={{ display: "block" }}>{ACTION_LABEL[p.action_type] ?? p.action_type}{p.summary?.object ? ` · ${p.summary.object}` : ""}</span>
                        <span className="meta">aguarda aprovação · {since(p.created_at)}</span></span>
                      {p.monetary_scope_minor > 0 && <span className="num">{formatMinor(p.monetary_scope_minor, p.currency)}</span>}
                    </button>))}
                    {waiting.length > 4 && <p className="faint" style={{ margin: 0 }}>e mais {waiting.length - 4} na Central de Aprovações</p>}</div>}
            </div>
            <div className="dk-card">
              <div className="card-title">Últimas entregas</div>
              {tasks === null ? <p className="muted">Carregando…</p> : tasks.length === 0
                ? <p className="muted" style={{ margin: 0 }}>Nenhuma tarefa registrada para este agente.</p>
                : <div className="rows">{tasks.slice(0, 3).map((t) => <TaskRow key={t.id} t={t} />)}</div>}
            </div>
            <div className="dk-card">
              <div className="card-title">Sobre</div>
              <dl className="dk-kv">
                <dt>Função</dt><dd>{a.role}</dd>
                <dt>Departamento</dt><dd>{look.department}</dd>
                <dt>O que sabe fazer</dt><dd>{skills === null ? "…" : skills.length === 0 ? "—" : skills.map((s) => s.title).join(" · ")}</dd>
                <dt>Restrições</dt><dd>Só informa e propõe; ações com efeito externo passam por aprovação humana.</dd>
              </dl>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="btn primary" onClick={() => desk.openChat(agentId)}><MessageSquare />Conversar</button>
              <button type="button" className="btn" onClick={() => setTab("tasks")}><Box />Abrir tarefas</button>
              <button type="button" className="btn" onClick={() => setTab("tools")}><Wrench />Ver custos</button>
              {onShowInOffice && <button type="button" className="btn ghost" onClick={() => onShowInOffice(agentId)}><MapPin />Ver no escritório</button>}
            </div>
          </>
        )}
        {tab === "tasks" && (
          <div className="dk-card">
            <div className="card-title">Tarefas recentes</div>
            {tasks === null ? <p className="muted">Carregando…</p> : tasks.length === 0
              ? <p className="muted" style={{ margin: 0 }}>Nenhuma tarefa registrada.</p>
              : <div className="rows">{tasks.map((t) => <TaskRow key={t.id} t={t} detailed />)}</div>}
          </div>
        )}
        {tab === "routines" && <AgentRoutines agentId={agentId} />}
        {tab === "chat" && (
          desk.can("ai:use") ? <AgentConversations agentId={agentId} /> : <p className="muted">Seu papel não permite conversar com agentes.</p>
        )}
        {tab === "tools" && (
          <>
            <div className="legacy"><AgentSkills token={desk.token} agentId={agentId} canCreate={desk.can("tasks:create")} refreshSignal={lastSeq} /></div>
            <div className="dk-card">
              <div className="card-title">Custo de IA deste agente (mês)</div>
              {!desk.can("finance:read") ? <p className="muted" style={{ margin: 0 }}>Requer permissão financeira.</p>
                : !usage ? <p className="muted" style={{ margin: 0 }}>Carregando…</p>
                  : mine.length === 0 ? <p className="muted" style={{ margin: 0 }}>Sem chamadas de IA neste mês.</p>
                    : <div className="rows">{mine.map((u) => (
                      <div key={u.model} className="row-item"><div className="grow"><div className="t">{u.model}</div>
                        <div className="meta">{u.calls} chamada(s){u.simulated ? " · simulado" : ""}</div></div>
                        <div className="num">{formatMicroUsd(u.cost_micro_usd)}</div></div>))}</div>}
            </div>
          </>
        )}
      </div>
    </aside>
  );

  function TaskRow({ t, detailed }: { t: AgentTask; detailed?: boolean }) {
    const summary = taskSummary(t);
    return (
      <div className="row-item">
        <div className="grow">
          <div className="t">{desk.taskTitle(t.kind)}</div>
          <div className="meta">{TASK_STATE[t.state] ?? t.state} · {since(t.created_at)}{t.simulated ? " · simulado" : ""}</div>
          {detailed && summary && <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>{summary}</div>}
          {detailed && t.error && <div style={{ color: "var(--critical)", fontSize: 13, marginTop: 4 }}>{t.error}</div>}
        </div>
        {detailed && <span className="faint">execução #{t.id.slice(0, 8)}</span>}
      </div>
    );
  }
}

/** Conversas com este agente (só do usuário); a conversa abre na tela Conversas, com layout completo (UX-03). */
function AgentConversations({ agentId }: { agentId: string }) {
  const desk = useDesk();
  const [items, setItems] = useState<{ id: string; title: string; last_at: string; messages: number }[] | null>(null);
  useEffect(() => { api.conversations(desk.token, agentId).then((r) => setItems(r.items)).catch(() => setItems([])); }, [desk.token, agentId]);
  return (
    <div className="dk-card">
      <div className="card-title">Conversas<button type="button" className="dk-link" onClick={() => desk.openChat(agentId)}><MessageSquare size={14} />Nova conversa</button></div>
      {items === null ? <p className="muted" style={{ margin: 0 }}>Carregando…</p> : items.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma conversa com este agente ainda.</p> : (
        <div className="rows">{items.slice(0, 8).map((c) => (
          <button key={c.id} type="button" className="row-item" style={{ border: 0, background: "none", textAlign: "left", width: "100%" }} onClick={() => desk.openChat(agentId, c.id)}>
            <span className="grow"><span className="t ellipsis" style={{ display: "block" }}>{c.title || "(sem título)"}</span><span className="meta">{c.messages} mensagens · {since(c.last_at)}</span></span>
          </button>))}</div>
      )}
    </div>
  );
}
