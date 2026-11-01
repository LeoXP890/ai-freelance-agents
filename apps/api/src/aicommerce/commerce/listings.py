"""Listings por canal: preparar (preço + compliance + payload do canal) → propor → aprovar → executar.

`compliance_gate(product, supplier, offer, channel, fulfillment_mode)` (SDD §9) devolve pendências BLOCKER/WARNING.
Com BLOCKER, a publicação não pode ser proposta. Cada preparação gera uma versão imutável; nova versão
expira propostas anteriores ainda não executadas (aprovação vinculada à versão).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.canonical import sha256_of
from aicommerce.commerce import pricing, suppliers
from aicommerce.config import get_settings
from aicommerce.governance import approvals, audit, golive
from aicommerce.models import (
    ApprovalProposal,
    ChannelCategoryMap,
    ChannelFeeSchedule,
    ChannelListing,
    ListingVersion,
    Product,
    Supplier,
    SupplierDocument,
    SupplierOffer,
    TaxProfile,
    Variant,
)
from aicommerce.office import events
from aicommerce.providers.marketplaces import (
    CONTRACT_STATUS,
    to_ml_item,
    to_shopee_item,
    to_tts_product,
    validate_payload,
)
from aicommerce.providers.woocommerce import to_woo_product, validate_product

CHANNELS = ("WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP")


class ListingError(Exception):
    code = "LISTING_ERROR"
    http = 409


class NotFound(ListingError):
    code, http = "NOT_FOUND", 404


class Blocked(ListingError):
    code = "COMPLIANCE_BLOCKED"


@dataclass(frozen=True)
class Issue:
    severity: str  # BLOCKER | WARNING
    code: str
    message: str


def compliance_gate(
    product: Product,
    variant: Variant,
    supplier: Supplier,
    offer: SupplierOffer,
    channel: str,
    fulfillment_mode: str,
    quote: pricing.Quote,
    now: dt.datetime | None = None,
) -> list[Issue]:
    now = now or dt.datetime.now(dt.UTC)
    out: list[Issue] = []

    def add(sev: str, code: str, msg: str) -> None:
        out.append(Issue(sev, code, msg))

    if product.status == "ARCHIVED" or variant.status != "ACTIVE":
        add("BLOCKER", "PRODUCT_INACTIVE", "produto arquivado ou variante inativa")
    if product.regulated and not product.regulatory_docs:
        add("BLOCKER", "REGULATED_WITHOUT_DOCS", "categoria regulada exige documentação específica antes de anunciar")
    if supplier.status != "APPROVED":
        add("BLOCKER", "SUPPLIER_NOT_APPROVED", f"fornecedor em {supplier.status}; aprovar o dossiê antes")
    if offer.supplier_id != supplier.id or offer.variant_id != variant.id:
        add("BLOCKER", "OFFER_MISMATCH", "oferta não pertence a esta variante/fornecedor")
    if offer.status != "ACTIVE" or (offer.valid_until is not None and offer.valid_until <= now):
        add("BLOCKER", "OFFER_EXPIRED", "oferta do fornecedor inativa ou vencida")
    if now - offer.stock_observed_at > suppliers.STOCK_FRESHNESS:
        add("BLOCKER", "STOCK_STALE", "estoque do fornecedor sem observação nas últimas 24 h")
    elif offer.stock_qty <= 0:
        add("BLOCKER", "OUT_OF_STOCK", "fornecedor sem estoque")
    if supplier.origin == "INTERNATIONAL" and fulfillment_mode != "CROSS_BORDER":
        add("BLOCKER", "FULFILLMENT_MISMATCH", "fornecedor internacional exige modalidade CROSS_BORDER (gate TAX-02)")
    if quote.breakdown is None:
        add("BLOCKER", "NO_PRICE", "sem preço viável para os custos informados")
    elif quote.breakdown.contribution_minor < 0:
        add("BLOCKER", "NEGATIVE_MARGIN", "margem de contribuição negativa")
    if not product.images:
        add("WARNING", "NO_IMAGES", "anúncio sem imagens tende a converter mal e pode ser recusado pelo canal")
    if not product.ncm:
        add("WARNING", "NO_NCM", "sem NCM: emissão fiscal ficará em FISCAL_HOLD")
    if channel in ("MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP") and not variant.gtin:
        add("WARNING", "NO_GTIN", "marketplace pode exigir GTIN/EAN")
    if CONTRACT_STATUS.get(channel) == "UNVERIFIED":
        add(
            "WARNING",
            "CONTRACT_UNVERIFIED",
            f"contrato {channel} não conferido em documentação oficial acessível; validar na homologação",
        )
    if channel == "TIKTOK_SHOP" and (supplier.origin == "INTERNATIONAL" or fulfillment_mode == "CROSS_BORDER"):
        add(
            "BLOCKER",
            "TTS_DOMESTIC_ONLY",
            "TikTok Shop BR exige fulfillment a partir de estoque/endereço no Brasil "
            "(restrição documentada no gate CH-TTS)",
        )
    if channel == "SHOPEE" and supplier.origin == "INTERNATIONAL":
        add(
            "WARNING",
            "SHOPEE_FULFILLMENT_UNCONFIRMED",
            "fulfillment internacional depende de confirmação oficial da Shopee (gate CH-SHO)",
        )
    for w in quote.warnings:
        add("WARNING", "PRICING", w)
    return out


def _fee_schedule(db: Session, channel: str) -> ChannelFeeSchedule:
    fs = (
        db.execute(
            select(ChannelFeeSchedule)
            .where(ChannelFeeSchedule.channel == channel, ChannelFeeSchedule.is_active.is_(True))
            .order_by(ChannelFeeSchedule.version.desc())
        )
        .scalars()
        .first()
    )
    if fs is None:
        raise ListingError(f"sem tabela de tarifas para {channel}")
    return fs


def _tax(db: Session) -> TaxProfile:
    tp = db.execute(select(TaxProfile).where(TaxProfile.is_default.is_(True))).scalars().first()
    if tp is None:
        raise ListingError("sem perfil tributário padrão")
    return tp


def pricing_input(
    db: Session,
    *,
    channel: str,
    cost_minor: int,
    shipping_minor: int,
    ads_cpa_minor: int,
    returns_bps: int,
    target_margin_bps: int,
) -> tuple[pricing.PricingInput, dict[str, Any]]:
    fs, tp = _fee_schedule(db, channel), _tax(db)
    inp = pricing.PricingInput(
        cost_minor=cost_minor,
        shipping_minor=shipping_minor,
        channel_fixed_fee_minor=fs.fixed_fee_minor,
        ads_cpa_minor=ads_cpa_minor,
        commission_bps=fs.commission_bps,
        payment_fee_bps=fs.payment_fee_bps,
        tax_bps=tp.effective_rate_bps,
        returns_bps=returns_bps,
        target_margin_bps=target_margin_bps,
        fee_schedule_verified=fs.verified,
        tax_approved=tp.approved_by_accountant,
    )
    params = {
        "fee_schedule": {
            "channel": channel,
            "version": fs.version,
            "verified": fs.verified,
            "source_note": fs.source_note,
        },
        "tax_profile": {"code": tp.code, "approved_by_accountant": tp.approved_by_accountant},
    }
    return inp, params


def _channel_payload(
    listing_id: str,
    channel: str,
    product: Product,
    variant: Variant,
    offer: SupplierOffer,
    price_minor: int,
    simulated: bool,
    category_id: str | None = None,
) -> dict[str, Any]:
    if channel == "WOOCOMMERCE":
        return to_woo_product(
            listing_id=listing_id,
            title=product.title,
            description=product.description,
            sku=variant.sku,
            price_minor=price_minor,
            stock_qty=offer.stock_qty,
            weight_g=variant.weight_g,
            dims_mm=(variant.length_mm, variant.width_mm, variant.height_mm),
            images=list(product.images),
            category=product.category,
            simulated=simulated,
        )
    if channel == "MERCADO_LIVRE":
        return to_ml_item(
            title=product.title,
            category_id=category_id or "",
            price_minor=price_minor,
            stock_qty=offer.stock_qty,
            images=list(product.images),
            sku=variant.sku,
            brand=product.brand,
            gtin=variant.gtin,
        )
    if channel == "SHOPEE":
        try:
            cat = int(category_id or "")
        except ValueError:
            cat = 0
        return to_shopee_item(
            title=product.title,
            description=product.description,
            price_minor=price_minor,
            stock_qty=offer.stock_qty,
            weight_g=variant.weight_g,
            dims_mm=(variant.length_mm, variant.width_mm, variant.height_mm),
            category_id=cat,
            sku=variant.sku,
        )
    return to_tts_product(
        title=product.title,
        description=product.description,
        category_id=category_id or "",
        price_minor=price_minor,
        stock_qty=offer.stock_qty,
        sku=variant.sku,
        weight_g=variant.weight_g,
    )


def prepare(
    db: Session,
    *,
    variant_id: uuid.UUID,
    offer_id: uuid.UUID,
    channel: str,
    fulfillment_mode: str,
    uf: str,
    price_minor: int | None,
    target_margin_bps: int,
    shipping_minor: int,
    ads_cpa_minor: int,
    returns_bps: int,
    actor: str,
) -> ChannelListing:
    if channel not in CHANNELS:
        raise ListingError(f"canal desconhecido {channel}")
    variant = db.get(Variant, variant_id)
    offer = db.get(SupplierOffer, offer_id)
    if variant is None or offer is None:
        raise NotFound("variante ou oferta inexistente")
    product = db.get(Product, variant.product_id)
    supplier = db.get(Supplier, offer.supplier_id)
    inp, params = pricing_input(
        db,
        channel=channel,
        cost_minor=offer.cost_minor,
        shipping_minor=shipping_minor,
        ads_cpa_minor=ads_cpa_minor,
        returns_bps=returns_bps,
        target_margin_bps=target_margin_bps,
    )
    chosen = price_minor or pricing.quote(inp).suggested_minor
    q = pricing.quote(inp, chosen)
    issues = compliance_gate(product, variant, supplier, offer, channel, fulfillment_mode, q)
    if any(i.code == "STOCK_STALE" for i in issues):
        suppliers.flag_stale_stock(db, [offer])

    listing = db.execute(
        select(ChannelListing)
        .where(ChannelListing.variant_id == variant.id, ChannelListing.channel == channel)
        .with_for_update()
    ).scalar_one_or_none()
    simulated = get_settings().is_dry_run
    if listing is None:
        listing = ChannelListing(
            id=uuid.uuid4(),
            variant_id=variant.id,
            offer_id=offer.id,
            channel=channel,
            price_minor=chosen or 0,
            fulfillment_mode=fulfillment_mode,
            uf=uf.upper(),
            channel_payload={},
            pricing={},
            is_simulated=simulated,
            version=0,
        )
        db.add(listing)
    elif listing.proposal_id:
        old = db.get(ApprovalProposal, listing.proposal_id, with_for_update=True)
        if old and old.status in ("PENDING_APPROVAL", "AUTHORIZED"):
            approvals.close_unstarted(
                db,
                old,
                status="EXPIRED",
                actor="system",
                reason=f"anúncio repreparado (nova versão {listing.version + 1})",
            )
    category_id = None
    if channel != "WOOCOMMERCE":
        cmap = db.execute(
            select(ChannelCategoryMap).where(
                ChannelCategoryMap.channel == channel, ChannelCategoryMap.internal_category == product.category
            )
        ).scalar_one_or_none()
        if cmap is None:
            issues.append(
                Issue(
                    "BLOCKER",
                    "CATEGORY_UNMAPPED",
                    f"categoria '{product.category}' sem categoria correspondente no {channel}",
                )
            )
        else:
            category_id = cmap.external_category_id
            if not cmap.verified:
                issues.append(Issue("WARNING", "CATEGORY_UNVERIFIED", "categoria do canal não verificada no canal"))
    payload = _channel_payload(str(listing.id), channel, product, variant, offer, chosen or 0, simulated, category_id)
    validator = validate_product if channel == "WOOCOMMERCE" else (lambda p: validate_payload(channel, p))
    for err in validator(payload):
        issues.append(Issue("BLOCKER", "CHANNEL_CONTRACT", err))
    listing.offer_id, listing.price_minor = offer.id, chosen or 0
    listing.fulfillment_mode, listing.uf = fulfillment_mode, uf.upper()
    listing.channel_payload = payload
    listing.pricing = {
        "quote": q.as_dict(),
        "params": params,
        "input": {
            "cost_minor": inp.cost_minor,
            "offer_stock_qty": offer.stock_qty,
            "shipping_minor": shipping_minor,
            "ads_cpa_minor": ads_cpa_minor,
            "returns_bps": returns_bps,
            "target_margin_bps": target_margin_bps,
        },
    }
    listing.compliance = [asdict(i) for i in issues]
    listing.version += 1
    if listing.status not in ("PUBLISHED",):
        listing.status = "DRAFT"
    db.flush()
    db.add(
        ListingVersion(
            listing_id=listing.id,
            version=listing.version,
            channel_payload=payload,
            payload_sha256=sha256_of(payload),
            pricing=listing.pricing,
            created_by=actor,
        )
    )
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="listing.prepared",
        subject_type="listing",
        subject_id=str(listing.id),
        payload={
            "version": listing.version,
            "channel": channel,
            "price_minor": listing.price_minor,
            "blockers": [i.code for i in issues if i.severity == "BLOCKER"],
        },
    )
    events.emit(
        db,
        "listing.prepared",
        agent_id="nova",
        payload={
            "listing_id": str(listing.id),
            "version": listing.version,
            "channel": channel,
            "blockers": sum(i.severity == "BLOCKER" for i in issues),
            "simulated": simulated,
        },
    )
    return listing


def propose_pause(
    db: Session, listing_id: uuid.UUID, *, actor: str, reason: str, request_id: str | None, nonce: str
) -> ApprovalProposal:
    """Pausa no canal via governança (PAUSE_LISTING é liberada pela política, inclusive com kill switch)."""
    from aicommerce.governance.service import submit_proposal

    listing = db.get(ChannelListing, listing_id, with_for_update=True)
    if listing is None:
        raise NotFound("anúncio inexistente")
    if listing.status != "PUBLISHED" or not listing.external_id:
        raise Blocked(f"anúncio em estado {listing.status}: nada a pausar no canal")
    data = {
        "action_type": "PAUSE_LISTING",
        "agent_id": "nova",
        "subject_type": "listing",
        "subject_id": str(listing.id),
        "subject_version": listing.version,
        "payload": {"channel": listing.channel, "external_id": listing.external_id, "reason": reason[:300]},
        "risk": "LOW",
        "evidence_refs": [f"listing:{listing.id}"],
        "monetary_scope_minor": 0,
        "currency": listing.currency,
        "channel_account_id": None,
        "idempotency_key": f"listing:{listing.id}:v{listing.version}:pause:{nonce}",
    }
    prop, _ = submit_proposal(db, data, actor=actor, request_id=request_id, golive_req=None)
    return prop


def ensure_publish_ready(db: Session, listing: ChannelListing) -> tuple[SupplierOffer, Supplier]:
    """Falha fechada se algo mudou desde a preparação, inclusive durante espera pela aprovação.

    Chamada tanto na proposta quanto no preflight de execução (nunca somente na UI).
    Anúncios antigos sem snapshot de estoque exigem nova preparação.
    """
    blockers = [i for i in listing.compliance if i["severity"] == "BLOCKER"]
    if blockers:
        raise Blocked("pendências impeditivas: " + "; ".join(f"{b['code']}: {b['message']}" for b in blockers))
    # Uma edição no catálogo marca PRODUCT_CHANGED como WARNING para preservar o diagnóstico
    # no anúncio publicado; mas uma nova proposta deve sempre exigir preparação atualizada.
    if any(i.get("code") == "PRODUCT_CHANGED" for i in listing.compliance):
        raise Blocked("produto alterado depois da preparação; prepare o anúncio novamente")

    offer = db.get(SupplierOffer, listing.offer_id)
    variant = db.get(Variant, listing.variant_id)
    if offer is None or variant is None:
        raise Blocked("oferta/variante removida depois da preparação; prepare novamente")
    supplier = db.get(Supplier, offer.supplier_id)
    product = db.get(Product, variant.product_id)
    if supplier is None or product is None:
        raise Blocked("produto/fornecedor indisponível; prepare novamente")

    saved = listing.pricing or {}
    previous = saved.get("input") or {}
    required = {"cost_minor", "offer_stock_qty", "shipping_minor", "ads_cpa_minor", "returns_bps", "target_margin_bps"}
    if not required.issubset(previous):
        raise Blocked("snapshot de preço/estoque incompleto; prepare novamente")
    inp, params = pricing_input(
        db,
        channel=listing.channel,
        cost_minor=offer.cost_minor,
        shipping_minor=previous["shipping_minor"],
        ads_cpa_minor=previous["ads_cpa_minor"],
        returns_bps=previous["returns_bps"],
        target_margin_bps=previous["target_margin_bps"],
    )
    current_quote = pricing.quote(inp, listing.price_minor)
    new_blockers = [
        issue
        for issue in compliance_gate(
            product, variant, supplier, offer, listing.channel, listing.fulfillment_mode, current_quote
        )
        if issue.severity == "BLOCKER"
    ]
    if new_blockers:
        raise Blocked("pendências novas: " + "; ".join(f"{i.code}: {i.message}" for i in new_blockers))
    if offer.cost_minor != previous.get("cost_minor") or offer.stock_qty != previous.get("offer_stock_qty"):
        raise Blocked("custo ou estoque do fornecedor mudou; prepare novamente para recalcular o anúncio")
    if params != saved.get("params") or current_quote.as_dict() != saved.get("quote"):
        raise Blocked("tarifas, tributos ou premissas de preço mudaram; prepare novamente")
    if listing.channel != "WOOCOMMERCE":
        category = db.execute(
            select(ChannelCategoryMap).where(
                ChannelCategoryMap.channel == listing.channel,
                ChannelCategoryMap.internal_category == product.category,
            )
        ).scalar_one_or_none()
        if category is None or str(category.external_category_id) != str(listing.channel_payload.get("category_id")):
            raise Blocked("mapeamento da categoria mudou; prepare novamente")
        if not get_settings().is_dry_run and not category.verified:
            raise Blocked("categoria não homologada no canal; publicação LIVE bloqueada")
    if not get_settings().is_dry_run:
        if not inp.fee_schedule_verified or not inp.tax_approved:
            raise Blocked("tarifas ou impostos não homologados; publicação LIVE bloqueada")
        if CONTRACT_STATUS.get(listing.channel) == "UNVERIFIED":
            raise Blocked("contrato do marketplace não verificado; publicação LIVE bloqueada")

    return offer, supplier


def propose_publish(db: Session, listing_id: uuid.UUID, *, actor: str, request_id: str | None) -> ApprovalProposal:
    from aicommerce.governance.service import submit_proposal

    listing = db.get(ChannelListing, listing_id, with_for_update=True)
    if listing is None:
        raise NotFound("anúncio inexistente")
    offer, supplier = ensure_publish_ready(db, listing)
    docs = (
        db.execute(
            select(SupplierDocument.id).where(
                SupplierDocument.supplier_id == supplier.id, SupplierDocument.verified_at.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    action = "UPDATE_LISTING_MATERIAL" if listing.external_id else "PUBLISH_LISTING"
    warnings = [i for i in listing.compliance if i["severity"] == "WARNING"]
    data = {
        "action_type": action,
        "agent_id": "nova",
        "subject_type": "listing",
        "subject_id": str(listing.id),
        "subject_version": listing.version,
        "payload": listing.channel_payload,
        "risk": "HIGH" if len(warnings) >= 4 else "MEDIUM",
        "evidence_refs": [
            f"listing_version:{listing.id}:v{listing.version}",
            f"offer:{offer.id}",
            f"supplier:{supplier.id}",
            *(f"supplier_document:{d}" for d in docs),
        ],
        "monetary_scope_minor": 0,
        "currency": listing.currency,
        "channel_account_id": None,
        "idempotency_key": f"listing:{listing.id}:v{listing.version}:{action.lower()}",
    }
    req = golive.GoLiveRequest(
        golive.Channel(listing.channel),
        golive.SupplierOrigin(supplier.origin),
        golive.FulfillmentMode(listing.fulfillment_mode),
        listing.uf,
        str(listing.variant_id),
        str(supplier.id),
    )
    prop, _ = submit_proposal(db, data, actor=actor, request_id=request_id, golive_req=req)
    listing.proposal_id = prop.id
    if prop.status in ("PENDING_APPROVAL", "AUTHORIZED"):
        listing.status = "PENDING_APPROVAL" if listing.status != "PUBLISHED" else listing.status
    return prop


def on_execution_result(db: Session, prop: ApprovalProposal, *, succeeded: bool, provider_ref: str | None) -> None:
    """Chamado pelo executor: reflete o resultado no listing da MESMA versão aprovada."""
    if prop.subject_type != "listing":
        return
    try:
        listing_id = uuid.UUID(prop.subject_id)
    except ValueError:  # assunto "listing" que não é um anúncio do catálogo (ex.: proposta manual)
        return
    listing = db.get(ChannelListing, listing_id, with_for_update=True)
    if listing is None or listing.version != prop.subject_version:
        return
    if succeeded:
        if prop.action_type == "PAUSE_LISTING":
            listing.status = "PAUSED"
        else:
            listing.status, listing.external_id = "PUBLISHED", provider_ref or listing.external_id
    elif listing.status != "PUBLISHED":
        listing.status = "ERROR"
