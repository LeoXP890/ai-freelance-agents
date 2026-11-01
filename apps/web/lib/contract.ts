// Espelho TypeScript de packages/contracts/*.v1.schema.json (fonte: apps/api office/contract.py).
// tests/contract.test.ts falha se os enums divergirem do schema exportado pelo backend.

export const AGENT_STATES = [
  "IDLE", "PLANNING", "WORKING", "TOOL_CALL", "WAITING_APPROVAL", "BLOCKED", "COMPLETED", "ERROR",
] as const;
export type AgentState = (typeof AGENT_STATES)[number];

export type Mode = "DRY_RUN" | "LIVE";

export interface OfficeEventV1 {
  schema_version: 1;
  seq: number;
  event_id: string;
  type: string;
  agent_id: string | null;
  task_id: string | null;
  state: AgentState | null;
  timestamp: string;
  mode: Mode;
  simulated: boolean;
  payload: Record<string, unknown>;
}

export type Accessory = "none" | "hat" | "glasses" | "headset" | "cap";

export interface PresenceV1 {
  agent_id: string;
  name: string;
  role: string;
  room: string;
  color: string;
  /** Aparência configurável (OFF-002); puramente visual. */
  appearance?: { accessory?: Accessory; skin?: string };
  state: AgentState;
  task_id: string | null;
  last_seq: number;
  /** quando o estado mudou pela última vez (opcional, aditivo ao v1) */
  last_event_at?: string | null;
}

export interface PresenceSnapshotV1 {
  schema_version: 1;
  as_of_seq: number;
  mode: Mode;
  simulated: boolean;
  emergency_stop: boolean;
  agents: PresenceV1[];
}

export const ROOMS: { id: string; label: string }[] = [
  { id: "ceo", label: "Sala do CEO" },
  { id: "research", label: "Pesquisa" },
  { id: "supply", label: "Suprimentos" },
  { id: "marketing", label: "Marketing" },
  { id: "store", label: "Loja e canais" },
  { id: "finance", label: "Finanças" },
  { id: "support", label: "Atendimento" },
  { id: "compliance", label: "Compliance" },
  { id: "meeting", label: "Reuniões" },
];

export const STATE_LABEL: Record<AgentState, string> = {
  IDLE: "Ocioso",
  PLANNING: "Planejando",
  WORKING: "Trabalhando",
  TOOL_CALL: "Chamando ferramenta",
  WAITING_APPROVAL: "Aguardando aprovação",
  BLOCKED: "Bloqueado",
  COMPLETED: "Concluído",
  ERROR: "Erro",
};

export const STATE_COLOR: Record<AgentState, string> = {
  IDLE: "#7b8798",
  PLANNING: "#6aa6ff",
  WORKING: "#2bc1b3",
  TOOL_CALL: "#ffb703",
  WAITING_APPROVAL: "#c77dff",
  BLOCKED: "#f08a5d",
  COMPLETED: "#77dfbf",
  ERROR: "#ff5c7a",
};
