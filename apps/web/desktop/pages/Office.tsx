"use client";
// Escritório 3D (UXD-017..020): a sede inteira no formato novo — 7 salas, 13 agentes nos lugares da sala real de
// cada um. Clique numa sala (ou na lista) foca a câmera e abre o painel do departamento com os trabalhos reais.
// Sem WebGL: vista 2D equivalente. A versão anterior do escritório segue em ?ui=legacy (rollback).
import dynamic from "next/dynamic";
import { Building2, ChevronsLeft, ChevronsRight, Inbox, RotateCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { type AgentTask, api } from "@/lib/api";
import { lookFor, since, STATE_UI } from "@/lib/agentIdentity";
import { ZONES, type ZoneId, zoneById, zoneOfRoom } from "@/lib/officeHq";
import { TASK_STATE } from "@/lib/workflow";
import AgentPortrait from "../AgentPortrait";
import { taskSummary, useDesk } from "../context";
import type { Quality } from "../office/HQ";
import { StateChip } from "../ui";
import Agents from "./Agents";

const HQ = dynamic(() => import("../office/HQ"), {
  ssr: false, loading: () => <div className="empty" style={{ height: "100%" }}><Building2 /><strong>Carregando o escritório…</strong></div>,
});

export const QUALITY_KEY = "aic.quality";
const BAR_KEY = "aic.office.agentbar";
const ACTIVE = new Set(["PLANNING", "WORKING", "TOOL_CALL", "WAITING_APPROVAL", "BLOCKED", "ERROR"]);

function webglOk(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch { return false; }
}

export default function Office({ selected, onSelect }: { selected: string | null; onSelect(id: string | null): void }) {
  const desk = useDesk();
  const [quality, setQuality] = useState<Quality>("auto");
  const [reduced, setReduced] = useState(false);
  const [gl, setGl] = useState<boolean | null>(null);
  const [focus, setFocus] = useState<ZoneId | null>(null);
  const [wide, setWide] = useState(false);
  useEffect(() => {
    try { setWide(localStorage.getItem(BAR_KEY) === "1"); } catch { /* */ }
  }, []);
  useEffect(() => {
    setGl(webglOk());
    try { const q = localStorage.getItem(QUALITY_KEY); if (q === "low" || q === "high" || q === "auto" || q === "balanced") setQuality(q); } catch { /* */ }
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(m.matches);
    const on = (e: MediaQueryListEvent) => setReduced(e.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  const agents = useMemo(() => desk.office.order.map((id) => desk.office.agents[id]).filter((a): a is NonNullable<typeof a> => !!a), [desk.office]);
  const busy = agents.filter((a) => ACTIVE.has(a.state)).length;
  const inZone = (z: ZoneId) => agents.filter((a) => zoneOfRoom(a.room) === z);
  const listed = focus ? inZone(focus) : agents;

  if (gl === false) {
    return (
      <div>
        <div className="mode-strip" role="status">Seu computador não oferece WebGL — mostrando a vista 2D equivalente.</div>
        <Agents />
      </div>
    );
  }
  return (
    <div className="office">
      <div className="office-canvas">
        <HQ agents={agents} selectedId={selected} onSelect={onSelect} focus={focus} onFocus={setFocus} reducedMotion={reduced}
          quality={quality} pendingApprovals={desk.pending ?? 0} background={desk.theme === "light" ? "#dfe7ee" : "#141b26"} />
      </div>
      <div className="office-hud">
        <div className="dk-card">
          <strong>{focus ? zoneById(focus).label : "Sede AI Commerce"}</strong>
          <span className="faint" style={{ display: "block" }}>
            {focus ? `${inZone(focus).length} agente(s) nesta sala` : `7 salas · ${agents.length} agentes · ${busy} em atividade`} · estados reais do backend
          </span>
        </div>
        {focus && <button type="button" className="btn" onClick={() => setFocus(null)}><RotateCcw />Sede inteira</button>}
        {desk.office.simulated && <span className="dk-chip demo">SIMULAÇÃO</span>}
        {!!desk.pending && desk.can("approvals:read") && (
          <button type="button" className="dk-chip warn" style={{ background: "var(--panel-bg)", cursor: "pointer" }} onClick={() => desk.go("approvals")}>
            <Inbox aria-hidden="true" />{desk.pending} aprovação(ões) pendente(s)
          </button>
        )}
      </div>
      <nav className="office-rooms" aria-label="Salas do escritório">
        {ZONES.map((z) => (
          <button key={z.id} type="button" aria-pressed={focus === z.id} onClick={() => setFocus(focus === z.id ? null : z.id)}
            style={{ ["--tag" as string]: z.wall }} title={`${inZone(z.id).length} agente(s)`}>
            <span className="sw" aria-hidden="true" />{z.label}
          </button>
        ))}
      </nav>
      <nav className={`office-agentbar ${wide ? "wide" : ""}`} aria-label="Agentes no escritório">
        <button type="button" className="bar-toggle" onClick={() => { setWide(!wide); try { localStorage.setItem(BAR_KEY, wide ? "0" : "1"); } catch { /* */ } }}
          aria-expanded={wide} aria-label={wide ? "Recolher lista de agentes" : "Expandir lista de agentes"}>
          {wide ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}{wide && <span>Agentes ({listed.length})</span>}
        </button>
        {listed.map((a) => {
          const st = STATE_UI[a.state] ?? STATE_UI.IDLE!;
          return (
            <button key={a.agent_id} type="button" aria-pressed={selected === a.agent_id} title={wide ? undefined : `${a.name} — ${st.label}`}
              aria-label={`${a.name}: ${st.label}`} onClick={() => { onSelect(a.agent_id); setFocus(zoneOfRoom(a.room)); }}>
              <span className="face"><AgentPortrait agentId={a.agent_id} size={30} /><span className={`dot ${st.tone}`} aria-hidden="true" /></span>
              {wide && <span className="who"><strong>{a.name}</strong><span className="faint">{lookFor(a.agent_id).short}</span></span>}
              {wide && <StateChip state={a.state} />}
            </button>
          );
        })}
      </nav>
      {focus && !selected && <RoomPanel zone={focus} onClose={() => setFocus(null)} onAgent={onSelect} />}
      <div className="office-help"><RotateCcw size={12} style={{ verticalAlign: -2 }} /> Clique numa sala para focar · arraste para girar · rolagem para zoom · botão direito move · <kbd>R</kbd> volta à sede</div>
    </div>
  );
}

/** Painel do departamento: agentes da sala e o que estão fazendo de verdade (tarefas do backend). */
function RoomPanel({ zone, onClose, onAgent }: { zone: ZoneId; onClose(): void; onAgent(id: string): void }) {
  const desk = useDesk();
  const z = zoneById(zone);
  const ids = useMemo(() => desk.office.order.filter((id) => zoneOfRoom(desk.office.agents[id]?.room ?? "") === zone), [desk.office, zone]);
  const [tasks, setTasks] = useState<AgentTask[] | null>(null);
  const seq = ids.map((id) => desk.office.agents[id]?.last_seq ?? 0).join();
  useEffect(() => {
    let alive = true;
    Promise.all(ids.map((id) => api.tasks(desk.token, id).then((r) => r.items).catch(() => [] as AgentTask[])))
      .then((all) => { if (alive) setTasks(all.flat().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 6)); });
    return () => { alive = false; };
  }, [desk.token, zone, seq]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <aside className="room-panel dk-card" aria-label={`Sala: ${z.label}`}>
      <div className="detail-head" style={{ padding: 0, border: 0 }}>
        <div><div className="faint">Departamento</div><h2 style={{ fontSize: 17 }}>{z.label}</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar sala"><X size={16} /></button>
      </div>
      <div className="rows">
        {ids.map((id) => {
          const a = desk.office.agents[id]!;
          return (
            <button key={id} type="button" className="row-item" style={{ border: 0, background: "none", textAlign: "left", width: "100%" }} onClick={() => onAgent(id)}>
              <AgentPortrait agentId={id} size={30} />
              <span className="grow"><span className="t">{a.name}</span><span className="meta" style={{ display: "block" }}>{since(a.last_event_at)}</span></span>
              <StateChip state={a.state} />
            </button>
          );
        })}
      </div>
      <h3 className="sec-title" style={{ marginTop: 12 }}>Trabalhos recentes</h3>
      {tasks === null ? <p className="muted" style={{ margin: 0 }}>Carregando…</p> : tasks.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma tarefa registrada para esta sala.</p> : (
        <ul className="room-tasks">
          {tasks.map((t) => (
            <li key={t.id}><strong>{desk.office.agents[t.agent_id]?.name ?? t.agent_id}</strong> · {desk.taskTitle(t.kind)}
              <span className="faint" style={{ display: "block" }}>{taskSummary(t) || (TASK_STATE[t.state]?.label ?? t.state)} · {since(t.created_at)}{t.simulated ? " · simulado" : ""}</span></li>
          ))}
        </ul>
      )}
    </aside>
  );
}
