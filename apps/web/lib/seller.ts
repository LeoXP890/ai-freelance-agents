// Perfil do vendedor (PF/PJ) e elegibilidade por canal — ADR 0006. A interface só MOSTRA o que o servidor decidiu:
// nada aqui libera venda. Canal sem elegibilidade comprovada aparece como "só simulação", com o motivo.

export type EligStatus = "SUPPORTED" | "CONDITIONAL" | "UNVERIFIED" | "UNSUPPORTED";
export const ELIG: Record<EligStatus, { label: string; tone: "ok" | "warn" | "crit" | "" }> = {
  SUPPORTED: { label: "Comprovada", tone: "ok" },
  CONDITIONAL: { label: "Condicional", tone: "warn" },
  UNVERIFIED: { label: "Não verificada", tone: "" },
  UNSUPPORTED: { label: "Incompatível", tone: "crit" },
};
export const SELLER_SHORT: Record<string, string> = { PERSON: "Pessoa Física", COMPANY: "Pessoa Jurídica" };

export interface EligibilityRow {
  channel: string; seller_type: string; status: EligStatus | null; registered: boolean;
  aspects: Record<string, { status: EligStatus; note: string; label: string }>;
  restrictions: string[]; pending: string[]; sources: { url: string; kind?: string; accessed?: string }[];
  scope?: string; policy_version?: string; version?: number; verified_at?: string | null; live_possible?: boolean; note: string;
}

/** Explicação curta do porquê o canal está (ou não) só em simulação para o perfil. */
export function liveExplanation(row: EligibilityRow | undefined, sellerType: string): { simOnly: boolean; text: string } {
  const who = SELLER_SHORT[sellerType] ?? sellerType;
  if (!row || (!row.registered && sellerType === "COMPANY")) {
    return { simOnly: false, text: "Conta apta comprovada pela homologação do canal (desenho original PJ)." };
  }
  if (row.status === "SUPPORTED" || row.status === "CONDITIONAL") {
    return { simOnly: false, text: `Elegibilidade ${ELIG[row.status].label.toLowerCase()} para ${who}; a venda real ainda exige as homologações aprovadas.` };
  }
  const bad = Object.values(row.aspects).filter((a) => a.status !== "SUPPORTED" && a.status !== "CONDITIONAL").map((a) => a.label.toLowerCase());
  const st = row.status ? ELIG[row.status].label.toLowerCase() : "não verificada";
  return { simOnly: true, text: `Só simulação: elegibilidade para ${who} ${st}${bad.length ? ` (${bad.slice(0, 3).join(", ")}${bad.length > 3 ? "…" : ""})` : ""}.` };
}
