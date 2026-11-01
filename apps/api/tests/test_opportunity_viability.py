import datetime as dt
import uuid

from sqlalchemy import select

from aicommerce.ai import opportunity_viability
from aicommerce.ai.opportunity_viability import assess
from aicommerce.ai.specs import SPECS
from aicommerce.models import SupplierOffer


def test_luna_without_cost_basis_cannot_recommend_commercially(db):
    ctx = SPECS["research.opportunities"].build(db, {"topic": "casa e cozinha"})
    result = ctx.facts["viabilidade_referencia"]
    assert result["status"] == "BLOCKED_MISSING_COST_BASIS"
    assert result["breakdown"] is None and result["commercial_recommendation_allowed"] is False


def test_margin_includes_freight_fees_taxes_and_returns(db):
    offer = db.scalars(select(SupplierOffer).where(SupplierOffer.stock_qty > 0)).first()
    result = assess(db, {"offer_id": str(offer.id), "sale_price_minor": 20000, "shipping_minor": 1700})
    b = result["breakdown"]
    assert b is not None
    assert b["contribution_minor"] == (
        20000 - b["fixed_minor"] - b["commission_minor"] - b["payment_fee_minor"] - b["tax_minor"] - b["returns_minor"]
    )
    assert b["fixed_minor"] >= offer.cost_minor + 1700
    assert result["commercial_recommendation_allowed"] is False
    assert result["source_parameters"]["fee_schedule"]["version"]


def test_stale_and_missing_offers_fail_closed(db):
    assert (
        assess(db, {"offer_id": str(uuid.uuid4()), "sale_price_minor": 100, "shipping_minor": 1})["status"]
        == "BLOCKED_INVALID_OFFER"
    )
    offer = db.scalars(select(SupplierOffer).where(SupplierOffer.stock_qty > 0)).first()
    original = offer.stock_observed_at
    try:
        offer.stock_observed_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
        assert (
            assess(db, {"offer_id": str(offer.id), "sale_price_minor": 100, "shipping_minor": 1})["status"]
            == "BLOCKED_STALE_OFFER"
        )
    finally:
        offer.stock_observed_at = original


def test_nexus_receives_direct_fulfillment_requirements(db):
    ctx = SPECS["supplier.discover"].build(db, {"category": "casa e cozinha"})
    assert ctx.facts["canal_prioritario"] == "MERCADO_LIVRE"
    assert ctx.facts["melhor_envio_obrigatorio"] is False
    assert len(ctx.facts["responsabilidade_desejada"]) == 4


def test_foreign_currency_cannot_be_used_as_brl_cost(db):
    offer = db.scalars(select(SupplierOffer).where(SupplierOffer.stock_qty > 0)).first()
    original = offer.currency
    try:
        offer.currency = "USD"
        result = assess(db, {"offer_id": str(offer.id), "sale_price_minor": 20000, "shipping_minor": 1700})
        assert result["status"] == "BLOCKED_CURRENCY" and result["breakdown"] is None
    finally:
        offer.currency = original


def test_missing_tariff_never_invents_margin(db, monkeypatch):
    offer = db.scalars(select(SupplierOffer).where(SupplierOffer.stock_qty > 0)).first()

    def no_fees(*args, **kwargs):
        raise opportunity_viability.listings.ListingError("missing schedule")

    monkeypatch.setattr(opportunity_viability.listings, "pricing_input", no_fees)
    result = assess(db, {"offer_id": str(offer.id), "sale_price_minor": 20000, "shipping_minor": 1700})
    assert result["status"] == "BLOCKED_MISSING_FEE_TAX_BASIS" and result["breakdown"] is None
