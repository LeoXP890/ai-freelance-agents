// Redutor puro do estado do escritório. Fonte de verdade = backend (snapshot + eventos ordenados por seq).
// Regras: aplica somente seq > lastSeq (descarta duplicados e atrasados); um snapshot mais novo substitui
// o estado; lacunas de seq são normais (IDs de transações revertidas) e não indicam perda.
import type { AgentState, OfficeEventV1, PresenceSnapshotV1, PresenceV1 } from "./contract";

export interface OfficeState {
  ready: boolean;
  lastSeq: number;
  mode: "DRY_RUN" | "LIVE" | null;
  simulated: boolean;
  emergencyStop: boolean;
  agents: Record<string, PresenceV1>;
  order: string[];
  feed: OfficeEventV1[];
  byAgent: Record<string, OfficeEventV1[]>;
  ignored: number;
}

export const FEED_MAX = 60;
export const AGENT_FEED_MAX = 15;

export const initialOffice: OfficeState = {
  ready: false, lastSeq: 0, mode: null, simulated: true, emergencyStop: false,
  agents: {}, order: [], feed: [], byAgent: {}, ignored: 0,
};

export function applySnapshot(state: OfficeState, snap: PresenceSnapshotV1): OfficeState {
  if (state.ready && snap.as_of_seq < state.lastSeq) return state; // snapshot velho não regride a cena
  const agents: Record<string, PresenceV1> = {};
  for (const a of snap.agents) agents[a.agent_id] = a;
  return {
    ...state,
    ready: true,
    lastSeq: snap.as_of_seq,
    mode: snap.mode,
    simulated: snap.simulated,
    emergencyStop: snap.emergency_stop,
    agents,
    order: snap.agents.map((a) => a.agent_id),
    feed: state.feed.filter((e) => e.seq <= snap.as_of_seq),
  };
}

export function applyEvent(state: OfficeState, ev: OfficeEventV1): OfficeState {
  if (!state.ready || ev.seq <= state.lastSeq) return { ...state, ignored: state.ignored + 1 };
  let agents = state.agents;
  let byAgent = state.byAgent;
  if (ev.agent_id) {
    const current = agents[ev.agent_id];
    if (current && ev.state) {
      agents = { ...agents, [ev.agent_id]: { ...current, state: ev.state as AgentState, task_id: ev.task_id,
                                             last_seq: ev.seq, last_event_at: ev.timestamp } };
    } else if (current) {
      // evento sem mudança de estado (ex.: uso de ferramenta) ainda é atividade: atualiza "há quanto tempo"
      agents = { ...agents, [ev.agent_id]: { ...current, last_seq: ev.seq, last_event_at: ev.timestamp } };
    }
    const list = [ev, ...(byAgent[ev.agent_id] ?? [])].slice(0, AGENT_FEED_MAX);
    byAgent = { ...byAgent, [ev.agent_id]: list };
  }
  let emergencyStop = state.emergencyStop;
  if (ev.type === "emergency_stop.activated") emergencyStop = true;
  if (ev.type === "emergency_stop.released") emergencyStop = false;
  return { ...state, lastSeq: ev.seq, agents, byAgent, emergencyStop,
           feed: [ev, ...state.feed].slice(0, FEED_MAX) };
}

export function applyEvents(state: OfficeState, evs: OfficeEventV1[]): OfficeState {
  return [...evs].sort((a, b) => a.seq - b.seq).reduce(applyEvent, state);
}

// Aparência do avatar alterada por este usuário (OFF-002). Outros clientes recebem no próximo snapshot.
export function applyAppearance(state: OfficeState, agentId: string, color: string,
  appearance: PresenceV1["appearance"]): OfficeState {
  const cur = state.agents[agentId];
  if (!cur) return state;
  return { ...state, agents: { ...state.agents, [agentId]: { ...cur, color, appearance } } };
}

export function agentsInRoom(state: OfficeState, room: string): PresenceV1[] {
  return state.order.map((id) => state.agents[id]).filter((a): a is PresenceV1 => !!a && a.room === room);
}
