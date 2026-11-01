// Estado de cada canal de venda (SDD v3 UX-07): `Mock`, `Não conectado`, `Conectado`, `Problema` + homologação.
// Nunca um "verde genérico": o rótulo diz exatamente o que é, e homologação pendente aparece mesmo se conectado.

export const CHANNELS = ["WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"] as const;
export type Channel = (typeof CHANNELS)[number];
export const CHANNEL_GATE: Record<Channel, string> = { WOOCOMMERCE: "CH-WOO", MERCADO_LIVRE: "CH-ML", SHOPEE: "CH-SHO", TIKTOK_SHOP: "CH-TTS" };

export interface Account { channel: string; status: string; expires_at: string | null; simulated: boolean; external_account_id: string }
export type Health = { kind: "mock" | "disconnected" | "connected" | "problem"; label: string; detail: string; tone: "ok" | "warn" | "crit" | "info" | "" };

const STATUS_TEXT: Record<string, string> = {
  REAUTH_REQUIRED: "a autorização foi recusada pelo canal — reconectar a conta",
  REVOKED: "conta revogada", REPLACED: "substituída por outra conta",
};

/** O token de acesso (6 h no ML) vencido NÃO é problema: o servidor renova sozinho antes de usar. Problema é o canal
 * ter recusado a renovação (REAUTH_REQUIRED) ou a conta ter sido revogada. */
export function channelHealth(channel: string, account: Account | undefined, simulatedMode: boolean, now = Date.now()): Health {
  if (account && account.status !== "CONNECTED") {
    return { kind: "problem", tone: "crit", label: "Problema", detail: STATUS_TEXT[account.status] ?? `conta ${account.status.toLowerCase()}` };
  }
  const idle = !!account?.expires_at && new Date(account.expires_at).getTime() < now;
  if (account?.simulated || (!account && simulatedMode && channel === "WOOCOMMERCE")) {
    return { kind: "mock", tone: "info", label: "Mock", detail: account ? "conta simulada (DRY_RUN)" : "adaptador simulado/sandbox local" };
  }
  if (!account) return { kind: "disconnected", tone: "", label: "Não conectado", detail: "nenhuma conta autorizada" };
  return { kind: "connected", tone: "ok", label: "Conectado",
    detail: `conta ${account.external_account_id}${idle ? " · acesso renova no próximo uso" : ""}` };
}

/** Conta que vale para o canal: a real (se houver) antes de uma simulada antiga; conectada antes de inativa. */
export function accountFor(channel: string, accounts: Account[], simulatedMode: boolean): Account | undefined {
  const mine = accounts.filter((a) => a.channel === channel && a.simulated === simulatedMode);
  return mine.find((a) => a.status === "CONNECTED") ?? mine.find((a) => a.status !== "REPLACED");
}

export function gatePending(state: string | undefined): boolean {
  return state !== "APPROVED" && state !== "APPROVED_WITH_RESTRICTIONS";
}
