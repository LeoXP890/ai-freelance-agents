"use client";
// UX-02 Diretório de agentes (UXD-008): legível, filtrável, grade/lista; contagens reais do PresenceStore.
import { LayoutGrid, List, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { lookFor, since, STATE_UI } from "@/lib/agentIdentity";
import AgentPortrait from "../AgentPortrait";
import { useDesk } from "../context";
import { Empty, StateChip } from "../ui";

const WORKING = new Set(["PLANNING", "WORKING", "TOOL_CALL"]);
const VIEW_KEY = "aic.desk.agentsView";

export default function Agents() {
  const desk = useDesk();
  const [q, setQ] = useState("");
  const [dept, setDept] = useState("");
  const [state, setState] = useState("");
  const [view, setView] = useState<"grid" | "list">("grid");
  useEffect(() => { try { if (localStorage.getItem(VIEW_KEY) === "list") setView("list"); } catch { /* */ } }, []);
  const choose = (v: "grid" | "list") => { setView(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* */ } };

  const all = useMemo(() => desk.office.order.map((id) => desk.office.agents[id]!).filter(Boolean), [desk.office]);
  const depts = useMemo(() => [...new Set(all.map((a) => lookFor(a.agent_id).department))].sort(), [all]);
  const counts = {
    working: all.filter((a) => WORKING.has(a.state)).length,
    waiting: all.filter((a) => a.state === "WAITING_APPROVAL").length,
    blocked: all.filter((a) => a.state === "BLOCKED" || a.state === "ERROR").length,
  };
  const shown = all.filter((a) => {
    const l = lookFor(a.agent_id);
    const text = `${a.name} ${a.role} ${l.department} ${l.short}`.toLowerCase();
    return (!q || text.includes(q.toLowerCase())) && (!dept || l.department === dept) && (!state || a.state === state);
  });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Agentes</h1>
          <p className="num">{all.length} agentes · {counts.working} trabalhando · {counts.waiting} aguardando aprovação · {counts.blocked} impedido(s) ou com erro
            {desk.office.simulated && <span className="faint"> · simulação</span>}</p>
        </div>
      </div>
      <div className="filters" role="search">
        <label className="search" style={{ minWidth: 280 }}>
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Buscar agente</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por nome, função ou área"
            style={{ border: 0, background: "none", color: "var(--text-main)", font: "inherit", outline: "none", width: "100%" }} />
        </label>
        <select className="field" aria-label="Departamento" value={dept} onChange={(e) => setDept(e.target.value)}>
          <option value="">Todos os departamentos</option>
          {depts.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select className="field" aria-label="Estado" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">Todos os estados</option>
          {Object.entries(STATE_UI).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <div className="dk-seg" role="group" aria-label="Modo de exibição" style={{ marginLeft: "auto" }}>
          <button type="button" aria-pressed={view === "grid"} onClick={() => choose("grid")}><LayoutGrid size={14} /> Grade</button>
          <button type="button" aria-pressed={view === "list"} onClick={() => choose("list")}><List size={14} /> Lista</button>
        </div>
      </div>

      {shown.length === 0 ? <Empty icon={<Search />} title="Nenhum agente com esses filtros">Limpe a busca ou escolha outro departamento.</Empty>
        : view === "grid" ? (
          <div className="agents-grid">
            {shown.map((a) => {
              const l = lookFor(a.agent_id);
              return (
                <button key={a.agent_id} type="button" className="agent-card" onClick={() => desk.openAgent(a.agent_id)}
                  aria-label={`Abrir agente ${a.name}`}>
                  <AgentPortrait agentId={a.agent_id} size={56} />
                  <div style={{ minWidth: 0 }}>
                    <div className="name">{a.name}</div>
                    <div className="role">{l.short}</div>
                    <div className="foot">
                      <StateChip state={a.state} />
                      <span className="faint">{l.department} · {since(a.last_event_at)}</span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="dk-card" style={{ padding: 0 }}>
            <table className="agent-table">
              <thead><tr><th scope="col">Agente</th><th scope="col">Função</th><th scope="col">Departamento</th><th scope="col">Estado</th><th scope="col">Atualizado</th></tr></thead>
              <tbody>
                {shown.map((a) => {
                  const l = lookFor(a.agent_id);
                  return (
                    <tr key={a.agent_id} tabIndex={0} onClick={() => desk.openAgent(a.agent_id)}
                      onKeyDown={(e) => { if (e.key === "Enter") desk.openAgent(a.agent_id); }} aria-label={`Abrir agente ${a.name}`}>
                      <td><span style={{ display: "flex", alignItems: "center", gap: 10 }}><AgentPortrait agentId={a.agent_id} size={32} /><strong>{a.name}</strong></span></td>
                      <td className="muted">{l.short}</td>
                      <td>{l.department}</td>
                      <td><StateChip state={a.state} /></td>
                      <td className="faint">{since(a.last_event_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}
