"""Deterministic economics for a referenced supplier offer; never a commercial authorization."""

import datetime as dt
import uuid
from dataclasses import asdict

from aicommerce.commerce import listings, pricing, suppliers
from aicommerce.models import SupplierOffer


def assess(db, inputs):
    missing = [key for key in ("offer_id", "sale_price_minor", "shipping_minor") if inputs.get(key) is None]
    base = {
        "channel": "MERCADO_LIVRE",
        "commercial_recommendation_allowed": False,
        "scope": "oferta de referência; não comprova margem dos produtos sugeridos por tendências",
    }
    if missing:
        return {**base, "status": "BLOCKED_MISSING_COST_BASIS", "missing": missing, "breakdown": None}
    try:
        offer_id = uuid.UUID(str(inputs["offer_id"]))
    except ValueError:
        return {**base, "status": "BLOCKED_INVALID_OFFER", "breakdown": None}
    offer = db.get(SupplierOffer, offer_id)
    if offer is None:
        return {**base, "status": "BLOCKED_INVALID_OFFER", "breakdown": None}
    if offer.currency != "BRL":
        return {**base, "status": "BLOCKED_CURRENCY", "breakdown": None}
    for key in ("sale_price_minor", "shipping_minor"):
        if type(inputs[key]) is not int or inputs[key] < (1 if key == "sale_price_minor" else 0):
            return {**base, "status": "BLOCKED_INVALID_COST", "breakdown": None}
    now = dt.datetime.now(dt.UTC)
    if (
        offer.status != "ACTIVE"
        or offer.stock_qty <= 0
        or offer.stock_observed_at > now
        or now - offer.stock_observed_at > suppliers.STOCK_FRESHNESS
        or (offer.valid_until is not None and offer.valid_until <= now)
    ):
        return {**base, "status": "BLOCKED_STALE_OFFER", "breakdown": None}
    supplier, documents, offers = suppliers.load(db, offer.supplier_id)
    dossier = suppliers.assess(supplier, documents, offers, now)
    try:
        calculation, parameters = listings.pricing_input(
            db,
            channel="MERCADO_LIVRE",
            cost_minor=offer.cost_minor,
            shipping_minor=inputs["shipping_minor"],
            ads_cpa_minor=0,
            returns_bps=200,
            target_margin_bps=1500,
        )
    except listings.ListingError:
        return {**base, "status": "BLOCKED_MISSING_FEE_TAX_BASIS", "breakdown": None}
    result = pricing.breakdown(calculation, inputs["sale_price_minor"])
    return {
        **base,
        "status": "NEGATIVE_MARGIN" if result.contribution_minor < 0 else "ESTIMATE_REQUIRES_EVIDENCE",
        "offer_id": str(offer.id),
        "variant_id": str(offer.variant_id),
        "supplier_id": str(supplier.id),
        "supplier_identity_and_dossier_ready": dossier.ready_for_approval,
        "cost_minor": offer.cost_minor,
        "shipping_minor": inputs["shipping_minor"],
        "breakdown": asdict(result),
        "fee_schedule_verified": calculation.fee_schedule_verified,
        "tax_approved": calculation.tax_approved,
        "source_parameters": parameters,
        "checked_at": now.isoformat(),
        "stock_observed_at": offer.stock_observed_at.isoformat(),
        "assumptions": [
            "frete informado, ainda exige cotação por destino",
            "CPA de mídia zero nesta estimativa",
            "provisão de devolução de 200 bps é hipótese, não custo comprovado",
            "embalagem, etiqueta, despacho, rastreio e elegibilidade PF exigem contrato e gates",
        ],
    }
