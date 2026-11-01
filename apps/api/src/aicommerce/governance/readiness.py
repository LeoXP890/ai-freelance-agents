"""Read-only, fail-closed overview of pending commercial homologations (Fase 3B).

This is a diagnostic, not permission to enable LIVE. A policy ALLOW result only
covers the *specific* request scope; it never checks provider credentials,
supplier contracts, marketplace account state or deployment health.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import golive, seller, service

ACTION_HINTS = {
    "LEG-01": "Validar elegibilidade do modelo de venda e registrar parecer aplicável ao perfil.",
    "OPS-01": "Revisar e registrar políticas de consumidor, privacidade e devolução.",
    "SUP-01": "Homologar fornecedor real, documentos, estoque e condições de expedição.",
    "TAX-01": "Confirmar obrigações fiscais nacionais para venda à ordem.",
    "TAX-02": "Confirmar obrigações fiscais para importação, se aplicável.",
    "TAX-03": "Homologar documento fiscal e emissor aplicável à venda.",
    "CH-WOO": "Homologar loja, pagamento, webhooks e conta WooCommerce.",
    "CH-ML": "Homologar conta Mercado Livre, API e modalidade de envio.",
    "CH-SHO": "Verificar regras da Shopee, conta elegível e acesso de parceiro.",
    "CH-TTS": "Verificar elegibilidade da TikTok Shop e acesso de parceiro.",
}


def report(
    db: Session,
    *,
    uf: str | None = None,
    supplier_origin: golive.SupplierOrigin | None = None,
    fulfillment_mode: golive.FulfillmentMode | None = None,
    product_id: str | None = None,
    supplier_id: str | None = None,
) -> dict[str, Any]:
    """Summarize go-live policy across channels without mutating gates or proposing actions."""
    profile = seller.get_profile(db)
    context = seller.context(db)
    gates = service.gate_snapshots(db)
    settings = get_settings()
    effective_uf = (uf or profile.uf or "").upper()
    missing_scope = [
        field
        for field, present in (
            ("uf", bool(effective_uf)),
            ("supplier_origin", supplier_origin is not None),
            ("fulfillment_mode", fulfillment_mode is not None),
        )
        if not present
    ]
    actions: dict[str, dict[str, Any]] = {}
    channels = []
    for channel in golive.Channel:
        eligibility = context.eligibility.get(channel.value)
        if missing_scope:
            decision = "NOT_EVALUATED"
            required_gates: list[str] = []
            unmet: dict[str, str] = {"SCOPE": "Escopo incompleto: " + ", ".join(missing_scope)}
            restrictions: dict[str, Any] = {}
        else:
            request = golive.GoLiveRequest(
                channel,
                supplier_origin,
                fulfillment_mode,
                effective_uf,
                product_id,
                supplier_id,
            )
            result = golive.evaluate(request, gates, context)
            decision = result.decision
            required_gates = result.required_gates
            unmet = result.unmet
            restrictions = result.restrictions
            for key, reason in unmet.items():
                item = actions.setdefault(
                    key,
                    {
                        "code": key,
                        "channels": [],
                        "action": (
                            "Confirmar separadamente cadastro, API, venda habitual, envio e documento fiscal "
                            "para esta conta e modalidade."
                            if key.startswith("ELIG:")
                            else ACTION_HINTS.get(key, "Verificar a evidência e o escopo exigidos.")
                        ),
                        "responsibility": "EXTERNAL_EVIDENCE_OR_OWNER_APPROVAL",
                        "reason_by_channel": {},
                    },
                )
                item["channels"].append(channel.value)
                item["reason_by_channel"][channel.value] = reason
        channels.append(
            {
                "channel": channel.value,
                "eligibility_status": eligibility.status if eligibility else "UNVERIFIED",
                "policy_decision": decision,
                "required_gates": required_gates,
                "unmet": unmet,
                "restrictions": restrictions,
                "provider_account_verified": False,  # no network call or credentials inspected here
                "commercial_activation_authorized": False,  # explicit approval/execution is separate
            }
        )
    return {
        "diagnostic_only": True,
        "mode": settings.mode.value,
        "seller_type": context.seller_type,
        "scope": {
            "uf": effective_uf or None,
            "supplier_origin": supplier_origin.value if supplier_origin else None,
            "fulfillment_mode": fulfillment_mode.value if fulfillment_mode else None,
            "product_id": product_id,
            "supplier_id": supplier_id,
        },
        "scope_complete": not missing_scope,
        "missing_scope": missing_scope,
        "channels": channels,
        "next_actions": [actions[key] for key in sorted(actions)],
        "live_operation_enabled_by_this_report": False,
        "notice": (
            "Diagnóstico somente leitura. Mesmo policy_decision=ALLOW não demonstra "
            "conta, logística, fornecedor, infraestrutura ou pagamento homologados; "
            "nenhum anúncio, compra, gasto ou LIVE foi habilitado."
        ),
    }
