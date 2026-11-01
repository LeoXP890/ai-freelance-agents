// Formatação de apresentação (valores monetários chegam em minor units inteiros do backend).

export function formatMinor(minor: number, currency = "BRL"): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(minor / 100);
}

export function shortHash(h: string): string {
  return h.length > 16 ? `${h.slice(0, 8)}…${h.slice(-6)}` : h;
}

export const STATUS_LABEL: Record<string, string> = {
  PENDING_APPROVAL: "Aguardando aprovação",
  AUTHORIZED: "Autorizada (na fila)",
  AUTO_AUTHORIZED: "Autorizada pela política",
  EXECUTING: "Executando",
  SUCCEEDED: "Executada",
  FAILED: "Falhou",
  UNKNOWN: "Resultado incerto (reconciliando)",
  REJECTED: "Rejeitada",
  CHANGES_REQUESTED: "Mudanças solicitadas",
  DENIED: "Negada pela política",
  REVOKED: "Revogada",
  EXPIRED: "Expirada",
};

export const ACTION_LABEL: Record<string, string> = {
  PUBLISH_LISTING: "Publicar anúncio",
  UPDATE_LISTING_MATERIAL: "Alterar anúncio",
  PAUSE_LISTING: "Pausar anúncio",
  CREATE_CAMPAIGN: "Criar campanha",
  INCREASE_BUDGET: "Aumentar orçamento",
  SPEND: "Despesa",
  PAUSE_MEDIA: "Pausar mídia",
  REFUND: "Reembolso",
  CHANGE_SUPPLIER: "Trocar fornecedor",
  SUPPLIER_PURCHASE: "Compra no fornecedor",
  RESEARCH: "Pesquisa",
  PREPARE_LISTING_DRAFT: "Rascunho de anúncio",
  PUBLISH_VIA_PLATFORM: "Publicar via Dropi",
};

/** Mensagens claras para os códigos de erro do inbox de aprovações. */
export function approvalErrorMessage(code: string | null, fallback: string): string {
  switch (code) {
    case "STALE_PAYLOAD": return "A proposta mudou desde que você abriu. Recarregue e revise antes de decidir.";
    case "POLICY_CHANGED": return `A política atual não permite mais esta ação. ${fallback}`;
    case "BUDGET_UNAVAILABLE": return `Sem orçamento disponível para reservar. ${fallback}`;
    case "INVALID_STATE": return `Esta proposta não aceita mais decisão. ${fallback}`;
    case "FORBIDDEN": return fallback;
    default: return fallback;
  }
}

/** Indica se a lista de eventos recentes afeta o inbox (para recarregar contagem/detalhe). */
export function affectsApprovals(type: string): boolean {
  return type.startsWith("approval.") || type.startsWith("execution.") || type.startsWith("listing.publish")
    || type === "integration.unknown_result" || type.startsWith("emergency_stop.");
}

/** Eventos que mudam fornecedores/anúncios (recarregam os painéis da Nexus e da Nova). */
export function affectsCommerce(type: string): boolean {
  return type.startsWith("supplier.") || type.startsWith("listing.") || type.startsWith("catalog.")
    || type.startsWith("order.") || type.startsWith("return.") || type.startsWith("settlement.")
    || type === "invoice.authorized" || type === "budget.reserved"
    || type.startsWith("webhook.") || type.startsWith("execution.") || type.startsWith("approval.")
    || type.startsWith("campaign.") || type.startsWith("emergency_stop.");
}

export const LISTING_STATUS: Record<string, string> = {
  DRAFT: "Rascunho", PENDING_APPROVAL: "Aguardando aprovação", PUBLISHED: "Publicado", PAUSED: "Pausado",
  ERROR: "Erro na publicação", DELETED_EXTERNALLY: "Removido no canal",
};

export const SUPPLIER_STATUS: Record<string, string> = {
  PROSPECT: "Prospecção", EVALUATING: "Em avaliação", APPROVED: "Aprovado (interno)", SUSPENDED: "Suspenso",
  REJECTED: "Rejeitado",
};

export function formatBps(bps: number): string {
  return `${(bps / 100).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

export function hoursSince(iso: string, now: number = Date.now()): number {
  return Math.floor((now - new Date(iso).getTime()) / 3_600_000);
}

export const ORDER_STATUS: Record<string, string> = {
  NEW: "Novo", PAYMENT_CONFIRMED: "Pago", FISCAL_PENDING: "Aguardando nota", READY_TO_FULFILL: "Pronto p/ comprar",
  SUPPLIER_ACCEPTED: "Fornecedor aceitou", SHIPPED: "Enviado", DELIVERED: "Entregue", CANCELLED: "Cancelado",
  RETURN_REQUESTED: "Devolução solicitada", REFUNDED: "Reembolsado", INCIDENT: "Incidente",
};

/** Próxima ação operacional sugerida para o pedido (só UI; o backend valida a transição). */
export function nextOrderAction(status: string, fulfillmentStatus: string | null): string | null {
  if (status === "READY_TO_FULFILL" && (!fulfillmentStatus || fulfillmentStatus === "CANCELLED")) return "fulfill";
  if (status === "SUPPLIER_ACCEPTED" && fulfillmentStatus === "ACCEPTED") return "ship";
  if (status === "SHIPPED" && fulfillmentStatus === "SHIPPED") return "deliver";
  return null;
}

/** Micro-dólares → texto (US$ com 5 casas abaixo de 1 centavo). */
export function formatMicroUsd(micro: number): string {
  const usd = micro / 1_000_000;
  return `US$ ${usd < 0.01 ? usd.toFixed(5) : usd.toFixed(2)}`;
}

/** Campos de entrada que cada tipo de tarefa de IA exige (a UI escolhe o controle adequado). */
export const SKILL_INPUT: Record<string, "topic" | "question" | "category" | "listing" | "order" | "offer" | "none"> = {
  "research.opportunities": "topic",
  "compliance.review": "question",
  "supplier.shortlist": "category",
  "commercial.model": "offer",
  "listing.audit": "listing",
  "creative.copy": "listing",
  "ads.plan": "listing",
  "support.reply_draft": "order",
  "finance.brief": "none",
  "analytics.report": "none",
  "logistics.sla_check": "none",
  "channel.plan": "none",
};

export const GATE_STATE: Record<string, string> = {
  NOT_STARTED: "Não iniciado", COLLECTING_EVIDENCE: "Coletando evidências", IN_REVIEW: "Em revisão",
  APPROVED: "Aprovado", APPROVED_WITH_RESTRICTIONS: "Aprovado c/ restrições", REJECTED: "Rejeitado", EXPIRED: "Expirado",
};

/** Escala linear 0..max → 0..100 (%), com piso visível para valores > 0. */
export function barPct(value: number, max: number): number {
  if (max <= 0 || value <= 0) return 0;
  return Math.max(2, Math.round((value / max) * 100));
}

export const PAYMENT_STATUS: Record<string, string> = {
  PENDING: "Aguardando pagamento", AUTHORIZED: "Autorizado", CAPTURED: "Pago", REFUNDED: "Reembolsado",
  CHARGEBACK: "Em contestação", FAILED: "Falhou",
};
export const FISCAL_STATUS: Record<string, string> = {
  NOT_REQUIRED: "Sem nota", PENDING: "Nota pendente", QUEUED: "Nota na fila", AUTHORIZED: "NF-e autorizada",
  AUTHORIZED_SIMULATED: "NF-e simulada", FISCAL_HOLD: "Nota retida", REJECTED: "Nota rejeitada", FAILED: "Falha na nota",
  CANCELLED: "Nota cancelada",
};
