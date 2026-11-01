"""Policy Engine versionado (SDD §8): ALLOW | REQUIRE_APPROVAL | REQUEST_CHANGES | DENY.

Mundo fechado: ação desconhecida = DENY. Avaliação é pura e determinística; o mesmo resultado é
reavaliado pelo executor antes do side effect (dupla validação, REQ-DEV-006).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from aicommerce.canonical import sha256_of


class Decision(enum.StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    DENY = "DENY"


class ActionType(enum.StrEnum):
    RESEARCH = "RESEARCH"
    PREPARE_LISTING_DRAFT = "PREPARE_LISTING_DRAFT"
    PUBLISH_LISTING = "PUBLISH_LISTING"
    UPDATE_LISTING_MATERIAL = "UPDATE_LISTING_MATERIAL"
    PAUSE_LISTING = "PAUSE_LISTING"
    CREATE_CAMPAIGN = "CREATE_CAMPAIGN"
    INCREASE_BUDGET = "INCREASE_BUDGET"
    SPEND = "SPEND"
    PAUSE_MEDIA = "PAUSE_MEDIA"
    REFUND = "REFUND"
    CHANGE_SUPPLIER = "CHANGE_SUPPLIER"
    SUPPLIER_PURCHASE = "SUPPLIER_PURCHASE"  # compra no fornecedor para atender pedido (v2026.10.2)
    PUBLISH_VIA_PLATFORM = "PUBLISH_VIA_PLATFORM"  # anúncio publicado por plataforma parceira (Dropi) (v2026.10.3)


RISK_SCORES = {"LOW": 10, "MEDIUM": 40, "HIGH": 75, "CRITICAL": 95}

POLICY_V1_VERSION = "2026.10.1"
POLICY_V1_RULES: dict[str, Any] = {
    # Lista congelada: versões publicadas de política nunca mudam de conteúdo (hash registrado).
    "known_actions": [
        "RESEARCH",
        "PREPARE_LISTING_DRAFT",
        "PUBLISH_LISTING",
        "UPDATE_LISTING_MATERIAL",
        "PAUSE_LISTING",
        "CREATE_CAMPAIGN",
        "INCREASE_BUDGET",
        "SPEND",
        "PAUSE_MEDIA",
        "REFUND",
        "CHANGE_SUPPLIER",
    ],
    "auto_allowed_actions": ["RESEARCH", "PREPARE_LISTING_DRAFT", "PAUSE_LISTING"],
    "require_approval_actions": [
        "PUBLISH_LISTING",
        "UPDATE_LISTING_MATERIAL",
        "CREATE_CAMPAIGN",
        "INCREASE_BUDGET",
        "SPEND",
        "REFUND",
        "CHANGE_SUPPLIER",
    ],
    "delegable_actions": ["PAUSE_MEDIA"],
    "evidence_required_actions": ["PUBLISH_LISTING", "UPDATE_LISTING_MATERIAL", "CREATE_CAMPAIGN", "CHANGE_SUPPLIER"],
    "monetary_actions": ["CREATE_CAMPAIGN", "INCREASE_BUDGET", "SPEND", "REFUND"],
    "blocked_by_emergency_stop": [
        "CREATE_CAMPAIGN",
        "INCREASE_BUDGET",
        "SPEND",
        "PUBLISH_LISTING",
        "UPDATE_LISTING_MATERIAL",
    ],
    "external_effect_actions": [
        "PUBLISH_LISTING",
        "UPDATE_LISTING_MATERIAL",
        "PAUSE_LISTING",
        "CREATE_CAMPAIGN",
        "SPEND",
        "PAUSE_MEDIA",
        "REFUND",
        "CHANGE_SUPPLIER",
    ],
    "high_risk_threshold": 70,
    "max_auto_monetary_minor": 0,
    "allowed_currencies": ["BRL"],
    "proposal_ttl_hours": 72,
}

# v2026.10.2 — operação de pedidos (COM-009): compra no fornecedor exige aprovação humana (obrigação financeira),
# mas NÃO é travada pelo emergency stop: o SDD manda manter o atendimento de pedidos já contratados.
# Reembolso deixa de exigir evidência de orçamento de mídia (vem de recebíveis, não de budget).
POLICY_V2_VERSION = "2026.10.2"
POLICY_V2_RULES: dict[str, Any] = {
    **POLICY_V1_RULES,
    "known_actions": [*POLICY_V1_RULES["known_actions"], "SUPPLIER_PURCHASE"],
    "require_approval_actions": [*POLICY_V1_RULES["require_approval_actions"], "SUPPLIER_PURCHASE"],
    "monetary_actions": [*POLICY_V1_RULES["monetary_actions"], "SUPPLIER_PURCHASE"],
    "external_effect_actions": [*POLICY_V1_RULES["external_effect_actions"], "SUPPLIER_PURCHASE"],
    "budgeted_actions": ["CREATE_CAMPAIGN", "INCREASE_BUDGET", "SPEND", "SUPPLIER_PURCHASE"],
}
# v2026.10.3 — piloto com plataforma de dropshipping (Dropi): o anúncio é publicado pela plataforma parceira na conta
# do proprietário, não pelo executor. Mesmo assim é efeito externo: exige aprovação humana, evidência, homologações de
# go-live em LIVE e trava no emergency stop. Sem execução automática (não está no registry do executor).
POLICY_V3_VERSION = "2026.10.3"
POLICY_V3_RULES: dict[str, Any] = {
    **POLICY_V2_RULES,
    "known_actions": [*POLICY_V2_RULES["known_actions"], "PUBLISH_VIA_PLATFORM"],
    "require_approval_actions": [*POLICY_V2_RULES["require_approval_actions"], "PUBLISH_VIA_PLATFORM"],
    "evidence_required_actions": [*POLICY_V2_RULES["evidence_required_actions"], "PUBLISH_VIA_PLATFORM"],
    "external_effect_actions": [*POLICY_V2_RULES["external_effect_actions"], "PUBLISH_VIA_PLATFORM"],
    "blocked_by_emergency_stop": [*POLICY_V2_RULES["blocked_by_emergency_stop"], "PUBLISH_VIA_PLATFORM"],
}
POLICY_CURRENT_VERSION = POLICY_V3_VERSION
POLICY_CURRENT_RULES = POLICY_V3_RULES
POLICY_HISTORY = (
    (POLICY_V1_VERSION, POLICY_V1_RULES),
    (POLICY_V2_VERSION, POLICY_V2_RULES),
    (POLICY_V3_VERSION, POLICY_V3_RULES),
)


@dataclass(frozen=True)
class PolicyInput:
    action_type: str
    risk_score: int
    evidence_refs: list[str]
    monetary_scope_minor: int
    currency: str
    mode: str  # DRY_RUN | LIVE


@dataclass(frozen=True)
class PolicyContext:
    emergency_stop: bool
    golive_decision: str | None  # resultado do GoLivePolicy quando a ação tem efeito externo em LIVE
    delegations: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reasons: list[str]
    policy_version: str
    policy_sha256: str


def evaluate(p: PolicyInput, ctx: PolicyContext, rules: dict[str, Any], version: str) -> PolicyResult:
    def result(decision: Decision, *reasons: str) -> PolicyResult:
        return PolicyResult(decision, list(reasons), version, sha256_of(rules))

    a = p.action_type
    if a not in rules["known_actions"]:
        return result(Decision.DENY, f"ação desconhecida '{a}' (mundo fechado)")
    if p.currency not in rules["allowed_currencies"]:
        return result(Decision.DENY, f"moeda {p.currency} não permitida")
    if p.monetary_scope_minor < 0:
        return result(Decision.DENY, "valor monetário negativo")
    if ctx.emergency_stop and a in rules["blocked_by_emergency_stop"]:
        return result(Decision.DENY, "emergency stop ativo: novas despesas/publicações travadas")
    if p.mode == "LIVE" and a in rules["external_effect_actions"] and ctx.golive_decision != "ALLOW":
        return result(Decision.DENY, "LIVE sem todos os gates de go-live aprovados")
    if p.mode not in ("LIVE", "DRY_RUN"):
        return result(Decision.DENY, f"modo inválido {p.mode}")

    if a in rules["evidence_required_actions"] and not p.evidence_refs:
        return result(Decision.REQUEST_CHANGES, "ação exige evidence_refs")
    if a in rules["monetary_actions"] and a != "REFUND" and p.monetary_scope_minor == 0:
        return result(Decision.REQUEST_CHANGES, "ação monetária exige monetary_scope_minor > 0")

    reasons: list[str] = []
    if a in rules["require_approval_actions"]:
        reasons.append("ação exige aprovação humana no MVP")
    if p.risk_score >= rules["high_risk_threshold"]:
        reasons.append(f"risco {p.risk_score} ≥ limiar {rules['high_risk_threshold']}")
    if p.monetary_scope_minor > rules["max_auto_monetary_minor"]:
        reasons.append("escopo monetário acima do limite automático")
    if a in rules["delegable_actions"] and a not in ctx.delegations:
        reasons.append("ação delegável sem delegação explícita vigente")
    if reasons:
        return result(Decision.REQUIRE_APPROVAL, *reasons)
    if a in rules["auto_allowed_actions"] or a in ctx.delegations:
        return result(Decision.ALLOW, "permitido pela política")
    return result(Decision.REQUIRE_APPROVAL, "sem regra de autorização automática")
