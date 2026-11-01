"use client";
// Estado compartilhado do app desktop: sessão, PresenceStore (o MESMO de lib/officeStore — diretório, cards,
// mini-mapa e 3D leem daqui) e contadores. Nada aqui é autoritativo: tudo vem da API/WS do backend.
import { createContext, useContext } from "react";
import type { AgentTask, Me } from "@/lib/api";
import type { OfficeState } from "@/lib/officeStore";
import type { Transport } from "@/lib/officeSync";

export type PageId = "command" | "office" | "approvals" | "agents" | "chat" | "missions" | "products" | "suppliers"
  | "channels" | "campaigns" | "orders" | "finance" | "reports" | "knowledge" | "gates" | "audit" | "settings";

export interface Desk {
  token: string;
  me: Me | null;
  office: OfficeState;
  transport: Transport;
  online: boolean;
  theme: "dark" | "light";      // tema resolvido (preferência do usuário ou do sistema operacional)
  themePref: "system" | "dark" | "light";
  setThemePref(p: "system" | "dark" | "light"): void;              // false = sem servidor: telas mostram dados em cache e mutações ficam bloqueadas
  syncedAt: number | null;      // último contato bem-sucedido com o servidor (ms)
  notify(text: string, tone?: "ok" | "warn" | "crit"): void;
  pending: number | null;
  refresh: number;              // incrementa a cada evento de negócio (re-carregar painéis)
  approvalsSignal: number;
  taskTitle(kind: string): string;
  can(perm: string): boolean;
  go(page: PageId): void;
  openAgent(id: string | null): void;
  chatTarget: { agentId: string; conversationId?: string; n: number } | null;
  openChat(agentId: string, conversationId?: string): void;
  refreshApprovals(): void;
  /** Abre a Central de Aprovações já com esta proposta selecionada (só seleciona — nunca decide). */
  openApproval(id: string): void;
  approvalFocus: { id: string; n: number } | null;     // decisão confirmada pelo servidor: recarrega contador e listas na hora
  replaceToken(t: string): void;
  logout(): void;
}

export const DeskContext = createContext<Desk | null>(null);
export function useDesk(): Desk {
  const d = useContext(DeskContext);
  if (!d) throw new Error("useDesk fora do DesktopApp");
  return d;
}

// Rótulos humanos para tipos de tarefa que não são habilidades de IA (as habilidades vêm de /v1/ai/specs).
export const TASK_FALLBACK: Record<string, string> = {
  "foundation.heartbeat": "Verificação de rotina",
  "research.mock": "Pesquisa de mercado",
  "mission.plan": "Plano de missão",
  "mission.summarize": "Resumo de missão",
};

export function taskSummary(t: AgentTask): string {
  const r = t.result as Record<string, unknown> | null;
  const s = (r?.summary ?? r?.message ?? r?.title) as string | undefined;
  return typeof s === "string" ? s : "";
}
