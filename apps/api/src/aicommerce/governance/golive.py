"""GoLivePolicy (SDD §3 + adendo PF/ADR 0006): todos os gates aplicáveis a canal × fornecedor × fulfillment × UF,
aprovados PARA o perfil de vendedor em vigor, mais a elegibilidade comprovada do canal para esse perfil — ou DENY.

Função pura sobre o estado dos gates e da matriz de elegibilidade — sem exceção por prompt, sem default permissivo.
O perfil (PF/PJ) é parâmetro obrigatório: nenhum tipo de vendedor dispensa gate ("PERSON → ALLOW" não existe).
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field
from typing import Any

from aicommerce.governance.seller import (
    PF_NOT_HOMOLOGATED,
    PROFILE_SENSITIVE_GATES,
    SellerContext,
    SellerType,
    eligibility_check,
)


class GateState(enum.StrEnum):
    NOT_STARTED = "NOT_STARTED"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    APPROVED_WITH_RESTRICTIONS = "APPROVED_WITH_RESTRICTIONS"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Channel(enum.StrEnum):
    WOOCOMMERCE = "WOOCOMMERCE"
    MERCADO_LIVRE = "MERCADO_LIVRE"
    SHOPEE = "SHOPEE"
    TIKTOK_SHOP = "TIKTOK_SHOP"


class SupplierOrigin(enum.StrEnum):
    NATIONAL = "NATIONAL"
    INTERNATIONAL = "INTERNATIONAL"


class FulfillmentMode(enum.StrEnum):
    DROPSHIP_SUPPLIER = "DROPSHIP_SUPPLIER"  # venda à ordem
    OWN_STOCK = "OWN_STOCK"
    CROSS_BORDER = "CROSS_BORDER"


CHANNEL_GATE = {
    Channel.WOOCOMMERCE: "CH-WOO",
    Channel.MERCADO_LIVRE: "CH-ML",
    Channel.SHOPEE: "CH-SHO",
    Channel.TIKTOK_SHOP: "CH-TTS",
}

# Catálogo inicial (SDD §3). Estado inicial: nenhum aprovado.
GATE_CATALOG: list[dict[str, str]] = [
    {
        "code": "LEG-01",
        "title": "Empresa e elegibilidade",
        "expected_evidence": "CNPJ, CNAE, regime, IE e licenças quando aplicáveis",
        "responsible": "Proprietário + contador",
        "blocks": "Toda venda real",
    },
    {
        "code": "TAX-01",
        "title": "Venda à ordem nacional",
        "expected_evidence": "Parecer escrito para fluxo concreto, NF-e e CFOPs",
        "responsible": "Contador",
        "blocks": "Venda nacional triangular",
    },
    {
        "code": "TAX-02",
        "title": "Importação",
        "expected_evidence": "Parecer de importação e operação por modalidade",
        "responsible": "Contador/despachante",
        "blocks": "Cross-border real",
    },
    {
        "code": "TAX-03",
        "title": "Emissão",
        "expected_evidence": "Credenciamento SEFAZ, certificado/provedor e teste homologação",
        "responsible": "Proprietário + fiscal",
        "blocks": "Emissão em produção",
    },
    {
        "code": "SUP-01",
        "title": "Fornecedor",
        "expected_evidence": "Identificação, contrato, SLA, estoque, documentação e amostra",
        "responsible": "Nexus + humano",
        "blocks": "Oferta do fornecedor",
    },
    {
        "code": "CH-WOO",
        "title": "WooCommerce",
        "expected_evidence": "Plugin checkout/pagamento/frete, webhooks e integração fiscal",
        "responsible": "Engenharia",
        "blocks": "Loja live",
    },
    {
        "code": "CH-ML",
        "title": "Mercado Livre",
        "expected_evidence": "Conta apta, política de expedição, OAuth e teste autorizado",
        "responsible": "Engenharia + proprietário",
        "blocks": "Mercado Livre live",
    },
    {
        "code": "CH-SHO",
        "title": "Shopee",
        "expected_evidence": "Resposta oficial da conta sobre fulfillment, tarifas, API ou integrador",
        "responsible": "Proprietário",
        "blocks": "Shopee live",
    },
    {
        "code": "CH-TTS",
        "title": "TikTok Shop",
        "expected_evidence": "Endereço/depósito BR elegível, política de fulfillment, conta e acesso API",
        "responsible": "Proprietário",
        "blocks": "TikTok Shop live",
    },
    {
        "code": "OPS-01",
        "title": "Consumidor e LGPD",
        "expected_evidence": "Termos, privacidade, arrependimento, atendimento, retorno e retenção",
        "responsible": "Responsável comercial + jurídico",
        "blocks": "Go-live",
    },
]


@dataclass(frozen=True)
class GateSnapshot:
    code: str
    state: str
    approved_by: str | None = None
    evidence_sha256: str | None = None
    policy_version: str | None = None
    valid_until: dt.datetime | None = None
    scope: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GoLiveRequest:
    channel: Channel
    supplier_origin: SupplierOrigin
    fulfillment_mode: FulfillmentMode
    uf: str
    product_id: str | None = None
    supplier_id: str | None = None


@dataclass(frozen=True)
class GoLiveResult:
    decision: str  # ALLOW | DENY
    required_gates: list[str]
    unmet: dict[str, str]
    restrictions: dict[str, Any]
    seller_type: str = ""
    summary: str = ""


def required_gates(req: GoLiveRequest) -> list[str]:
    gates = ["LEG-01", "OPS-01", "SUP-01", "TAX-03", CHANNEL_GATE[req.channel]]
    if req.supplier_origin is SupplierOrigin.INTERNATIONAL or req.fulfillment_mode is FulfillmentMode.CROSS_BORDER:
        gates.append("TAX-02")
    if req.fulfillment_mode is FulfillmentMode.DROPSHIP_SUPPLIER and req.supplier_origin is SupplierOrigin.NATIONAL:
        gates.append("TAX-01")
    return sorted(set(gates))


def _unmet_reason(
    g: GateSnapshot | None, req: GoLiveRequest, now: dt.datetime, seller_type: str, fingerprints: dict[str, str]
) -> str | None:
    if g is None:
        return "gate inexistente"
    if g.state not in (GateState.APPROVED.value, GateState.APPROVED_WITH_RESTRICTIONS.value):
        return f"estado {g.state}"
    if not (g.approved_by and g.evidence_sha256 and g.policy_version):
        return "aprovação sem approved_by/hash de evidência/versão de política"
    if g.valid_until is None or g.valid_until <= now:
        return "aprovação sem validade ou expirada"
    if g.code in PROFILE_SENSITIVE_GATES:
        types = g.scope.get("seller_types")
        # aprovação vale só para o perfil em que foi dada; aprovações antigas (sem perfil) eram do desenho PJ
        if (types is None and seller_type != SellerType.COMPANY.value) or (
            types is not None and seller_type not in types
        ):
            return f"aprovado para outro perfil de vendedor ({', '.join(types or ['COMPANY'])}); requer revisão"
        # aprovação no formato novo vale só para as propriedades do perfil (e a elegibilidade) com que foi dada
        if types is not None and g.scope.get("profile_fingerprint") != fingerprints.get(g.code):
            return "perfil do vendedor ou elegibilidade do canal mudou desde a aprovação; requer nova revisão"
    for key, value in (
        ("channels", req.channel.value),
        ("ufs", req.uf.upper()),
        ("fulfillment_modes", req.fulfillment_mode.value),
        ("supplier_ids", req.supplier_id),
        ("product_ids", req.product_id),
    ):
        allowed = g.scope.get(key)
        if allowed is not None and value not in allowed:
            return f"fora do escopo aprovado ({key})"
    return None


def evaluate(
    req: GoLiveRequest, gates: dict[str, GateSnapshot], seller: SellerContext, now: dt.datetime | None = None
) -> GoLiveResult:
    now = now or dt.datetime.now(dt.UTC)
    needed = required_gates(req)
    unmet: dict[str, str] = {}
    restrictions: dict[str, Any] = {}
    for code in needed:
        reason = _unmet_reason(gates.get(code), req, now, seller.seller_type, seller.fingerprints)
        if reason:
            unmet[code] = reason
        elif gates[code].state == GateState.APPROVED_WITH_RESTRICTIONS.value:
            restrictions[code] = gates[code].scope.get("restrictions", "ver escopo")
    elig_reason, elig_restrictions = eligibility_check(seller, req)
    if elig_reason:
        unmet[f"ELIG:{req.channel.value}"] = elig_reason
    elif elig_restrictions:
        restrictions[f"ELIG:{req.channel.value}"] = elig_restrictions
    if unmet:
        summary = PF_NOT_HOMOLOGATED if seller.seller_type == SellerType.PERSON.value else "Homologações pendentes."
    else:
        summary = "Todas as homologações e a elegibilidade aplicáveis estão aprovadas e válidas."
    return GoLiveResult("DENY" if unmet else "ALLOW", needed, unmet, restrictions, seller.seller_type, summary)
