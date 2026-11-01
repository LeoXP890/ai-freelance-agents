"use client";
import { useEffect, useState } from "react";
import { api, ApiError, type AgentTask, type ProposalInput } from "@/lib/api";
import { type Accessory, type PresenceV1, STATE_COLOR, STATE_LABEL } from "@/lib/contract";
import type { OfficeState } from "@/lib/officeStore";
import { AgentChat, AgentSkills, AiCost, AtlasMissions, LunaOpportunities, TaskResult } from "./AiPanels";
import { GatesPanel, SalesPanel } from "./GovPanels";
import { CampaignsPanel, OrionPanel } from "./AdsPanels";
import CatalogEditor from "./CatalogEditor";
import NexusPanel from "./NexusPanel";
import { FinancePanel, OrdersPanel } from "./OpsPanels";
import NovaPanel from "./NovaPanel";

// Propostas de demonstração (DRY_RUN) por agente: passam pelo policy engine e pela caixa de aprovações.
async function demoProposal(token: string, agentId: string): Promise<ProposalInput | null> {
  const key = `demo-${agentId}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  if (agentId === "pixel") {
    const fin = await api.finance(token);
    const acc = fin.accounts.find((a) => a.simulated && typeof a.account_id === "string");
    if (!acc) return null;
    return {
      action_type: "CREATE_CAMPAIGN", agent_id: "pixel", subject_type: "campaign", subject_id: "DEMO-CAMP-01",
      subject_version: Math.floor(Date.now() / 1000), risk: "MEDIUM", evidence_refs: ["plano:demo-midia"],
      payload: { name: "Teste garrafa térmica (SIMULADO)", daily_budget_minor: 2000, days: 3 },
      monetary_scope_minor: 6000, budget_account_id: acc.account_id as string, idempotency_key: key,
    };
  }
  return null;
}

const ACCESSORY_LABEL: Record<Accessory, string> = {
  none: "Nenhum", hat: "Chapéu", glasses: "Óculos", headset: "Headset", cap: "Boné",
};

// Aparência do avatar (OFF-002): só visual; não altera ferramentas, limites nem permissões do agente.
function AppearanceEditor({ token, agent, onSaved }: {
  token: string; agent: PresenceV1; onSaved(color: string, appearance: PresenceV1["appearance"]): void;
}) {
  const [color, setColor] = useState(agent.color);
  const [accessory, setAccessory] = useState<Accessory>(agent.appearance?.accessory ?? "none");
  const [skin, setSkin] = useState(agent.appearance?.skin ?? "#f1e3d3");
  const [msg, setMsg] = useState<string | null>(null);
  async function save() {
    setMsg(null);
    try {
      const r = await api.setAppearance(token, agent.agent_id, color, accessory, skin);
      onSaved(r.color, r.appearance);
      setMsg("Aparência salva.");
    } catch (e) {
      setMsg(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  }
  return (
    <details className="appearance">
      <summary>Aparência do avatar</summary>
      <div className="appearance-row">
        <label>Cor <input type="color" value={color} onChange={(e) => setColor(e.target.value)} /></label>
        <label>Pele <input type="color" value={skin} onChange={(e) => setSkin(e.target.value)} /></label>
        <label>Acessório{" "}
          <select value={accessory} onChange={(e) => setAccessory(e.target.value as Accessory)}>
            {(Object.keys(ACCESSORY_LABEL) as Accessory[]).map((k) => <option key={k} value={k}>{ACCESSORY_LABEL[k]}</option>)}
          </select>
        </label>
        <button type="button" onClick={save}>Salvar</button>
      </div>
      {msg && <p className="muted small" role="status">{msg}</p>}
    </details>
  );
}

export default function AgentPanel({ office, agentId, token, canCreate, canPropose, onProposed, onClose,
  permissions, commerceSignal, onAppearance }: {
  office: OfficeState; agentId: string; token: string; canCreate: boolean; canPropose: boolean;
  onProposed(): void; onClose(): void; permissions: string[]; commerceSignal: number;
  onAppearance(agentId: string, color: string, appearance: PresenceV1["appearance"]): void;
}) {
  const agent = office.agents[agentId];
  const events = office.byAgent[agentId] ?? [];
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lastSeq = agent?.last_seq ?? 0;

  useEffect(() => {
    let alive = true;
    api.tasks(token, agentId).then((r) => alive && setTasks(r.items)).catch(() => alive && setTasks([]));
    return () => { alive = false; };
  }, [agentId, token, lastSeq]);

  if (!agent) return null;
  const proposes = agentId === "pixel";

  async function propose() {
    setBusy(true);
    setError(null);
    try {
      const body = await demoProposal(token, agentId);
      if (!body) throw new Error("conta de orçamento simulada não encontrada");
      const p = await api.propose(token, body);
      if (p.status === "PENDING_APPROVAL") onProposed();
      else setError(`Proposta ${p.status}: ${p.policy_reasons.join("; ")}`);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="panel" aria-label={`Painel do agente ${agent.name}`}>
      <header className="panel-head">
        <div>
          <div className="muted small">{agent.room.toUpperCase()}</div>
          <h2>{agent.name}</h2>
          <div className="muted">{agent.role}</div>
        </div>
        <button type="button" onClick={onClose} aria-label="Fechar painel">✕</button>
      </header>
      <div className="state-chip" style={{ borderColor: STATE_COLOR[agent.state], color: STATE_COLOR[agent.state] }}>
        {STATE_LABEL[agent.state]}
      </div>
      {permissions.includes("users:manage") && (
        <AppearanceEditor token={token} agent={agent} onSaved={(c, a) => onAppearance(agentId, c, a)} />
      )}
      <dl className="kv">
        <dt>Tarefa atual</dt><dd>{agent.task_id ? <code>{agent.task_id.slice(0, 8)}</code> : "—"}</dd>
        <dt>Último evento</dt><dd>seq {agent.last_seq}</dd>
      </dl>

      <AgentSkills token={token} agentId={agentId} canCreate={canCreate} refreshSignal={commerceSignal} />
      {agentId === "atlas" && permissions.includes("ai:missions") && (
        <AtlasMissions token={token} refreshSignal={lastSeq} />
      )}
      {agentId === "luna" && permissions.includes("catalog:read") && (
        <LunaOpportunities token={token} canPropose={canPropose} refreshSignal={lastSeq} onProposed={onProposed} />
      )}
      {agentId === "finn" && permissions.includes("finance:read") && <AiCost token={token} refreshSignal={lastSeq} />}
      {agentId === "clara" && permissions.includes("golive:evaluate") && (
        <GatesPanel token={token} canManage={permissions.includes("gates:manage")}
          canApprove={permissions.includes("gates:approve")} refreshSignal={lastSeq} />
      )}
      {agentId === "orion" && permissions.includes("dashboard:read") && (
        <SalesPanel token={token} refreshSignal={commerceSignal} />
      )}
      {permissions.includes("ai:use") && <AgentChat token={token} agentId={agentId} />}

      {canPropose && proposes && (
        <div className="block">
          <button type="button" disabled={busy || office.emergencyStop} onClick={propose}>
            Propor campanha de R$ 60 (DRY_RUN)
          </button>
          <p className="muted small">Vai para a caixa de aprovações da sala do CEO; nada é executado sem aprovação.</p>
        </div>
      )}

      {agentId === "nexus" && permissions.includes("catalog:read") && (
        <NexusPanel token={token} canWrite={permissions.includes("catalog:write")}
          canApprove={permissions.includes("suppliers:approve")} refreshSignal={commerceSignal} />
      )}
      {agentId === "nova" && permissions.includes("catalog:read") && (
        <NovaPanel token={token} canPrepare={permissions.includes("listings:prepare") && canPropose}
          refreshSignal={commerceSignal} onProposed={onProposed} />
      )}
      {agentId === "nova" && permissions.includes("catalog:write") && (
        <CatalogEditor token={token} refreshSignal={commerceSignal} />
      )}

      {(agentId === "logan" || agentId === "eva") && permissions.includes("orders:read") && (
        <OrdersPanel token={token} mode={agentId} perms={permissions} refreshSignal={commerceSignal}
          onProposed={onProposed} />
      )}
      {agentId === "pixel" && permissions.includes("finance:read") && (
        <CampaignsPanel token={token} perms={permissions} refreshSignal={commerceSignal} onProposed={onProposed} />
      )}
      {agentId === "orion" && permissions.includes("finance:read") && (
        <OrionPanel token={token} refreshSignal={commerceSignal} />
      )}
      {agentId === "finn" && permissions.includes("finance:read") && (
        <FinancePanel token={token} perms={permissions} refreshSignal={commerceSignal} />
      )}

      <h3>Tarefas recentes</h3>
      {tasks.length === 0 ? <p className="muted small">Nenhuma tarefa.</p> : (
        <ul className="list">
          {tasks.map((t) => (
            <li key={t.id}>
              <code>{t.id.slice(0, 8)}</code> {t.kind} — <strong>{t.state}</strong>
              {t.simulated && <span className="sim">Simulado</span>}
              {t.error && <div className="err small">{t.error}</div>}
              <TaskResult t={t} />
            </li>
          ))}
        </ul>
      )}

      <h3>Eventos (desde a conexão)</h3>
      {events.length === 0 ? <p className="muted small">Aguardando eventos do backend…</p> : (
        <ol className="list">
          {events.map((e) => (
            <li key={e.seq}>
              <span className="muted small">#{e.seq}</span> {e.type}
              {e.state && <span style={{ color: STATE_COLOR[e.state] }}> · {STATE_LABEL[e.state]}</span>}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
