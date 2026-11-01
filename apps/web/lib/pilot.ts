// Piloto Mercado Livre (31/10/2026): valores sugeridos para as decisões do proprietário. São só sugestões
// preenchidas no formulário — quem decide e confirma (com MFA) é o proprietário; o servidor valida tudo.
import type { GateDecision } from "./api";

export const PILOT_SCOPE = { channels: ["MERCADO_LIVRE"], ufs: ["PR"], fulfillment_modes: ["DROPSHIP_SUPPLIER"] };
const PILOT = "piloto: só Mercado Livre, até 2 produtos, dropship nacional pelo Dropi, UF PR";
const RISK = new Set(["LEG-01", "TAX-01", "TAX-02", "TAX-03"]);
const RESTRICTION: Record<string, string> = {
  "SUP-01": "sem amostra física e CNPJ dos fornecedores não conferido; estoque acompanhado a cada pedido",
  "OPS-01": "atendimento e devolução só pelas regras do Mercado Livre (Compra Garantida)",
};

export function suggestedDecision(code: string, days = 30): GateDecision & { restrictionsText: string } {
  const restricted = RISK.has(code) || code in RESTRICTION;
  const restrictionsText = restricted ? [PILOT, RESTRICTION[code]].filter(Boolean).join("; ") : "";
  return {
    decision: restricted ? "APPROVED_WITH_RESTRICTIONS" : "APPROVED",
    scope: PILOT_SCOPE,
    valid_until: new Date(Date.now() + days * 86400_000).toISOString(),
    note: RISK.has(code)
      ? "Aceite de risco do proprietário: venda como pessoa física, sem MEI e sem parecer, restrita ao piloto."
      : "Evidências conferidas pelo proprietário para o piloto no Mercado Livre.",
    restrictionsText,
  };
}

const SRC = "https://github.com/LeoXP890/ai-commerce/blob/62672b1/docs/readiness/piloto/EVIDENCIA_CH-ML.md";
/** Elegibilidade ML para Pessoa Física verificada na conta do proprietário em 31/10/2026 (com o que NÃO foi provado). */
export const ML_PERSON_ELIGIBILITY = {
  seller_type: "PERSON",
  aspects: {
    registration: { status: "SUPPORTED", note: "conta de vendedor CPF ativa; OAuth GET /users/me HTTP 200 em 31/10/2026" },
    habitual_sales: { status: "CONDITIONAL", note: "conta PF vende normalmente; enquadramento da habitualidade é risco assumido pelo proprietário" },
    api_access: { status: "SUPPORTED", note: "aplicação própria com OAuth funcionando na conta PF e integração Dropi conectada" },
    dropshipping: { status: "CONDITIONAL", note: "fornecedor do Dropi despacha com etiqueta Mercado Envios; só no piloto" },
    shipping: { status: "SUPPORTED", note: "Mercado Envios (ME2) ativo; cotação de frete do vendedor HTTP 200" },
    fiscal: { status: "CONDITIONAL", note: "sem emissão própria de NF-e; aceite de risco do proprietário, sem parecer" },
  },
  restrictions: [PILOT, "fiscal: aceite de risco do proprietário, sem parecer profissional"],
  conditions: [
    { field: "ufs", allowed: ["PR"], note: "UF do vendedor" },
    { field: "fulfillment_modes", allowed: ["DROPSHIP_SUPPLIER"], note: "só venda à ordem pelo Dropi" },
  ],
  pending: ["conta sem reputação: acompanhar as primeiras vendas"],
  sources: [{ url: SRC, kind: "evidência verificada na conta do proprietário", accessed: "2026-10-31" }],
  scope: PILOT,
};
