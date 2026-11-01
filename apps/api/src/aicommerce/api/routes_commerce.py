"""Catálogo mestre, fornecedores, ofertas, preço/margem, anúncios e webhooks (COM-001..004)."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aicommerce.api.deps import request_id, require
from aicommerce.api.routes_governance import proposal_out
from aicommerce.canonical import sha256_of
from aicommerce.commerce import catalog, listings, pricing, suppliers, webhooks
from aicommerce.config import get_settings
from aicommerce.db import get_db
from aicommerce.governance import audit
from aicommerce.identity.security import Principal
from aicommerce.models import (
    ChannelListing,
    ListingVersion,
    Product,
    Supplier,
    SupplierDocument,
    SupplierOffer,
    Variant,
)
from aicommerce.office import events
from aicommerce.providers.woocommerce import verify_webhook_signature

router = APIRouter(prefix="/v1", tags=["commerce"])
Channel = Literal["WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"]


def _err(e: Exception) -> HTTPException:
    return HTTPException(getattr(e, "http", 409), {"code": getattr(e, "code", "ERROR"), "message": str(e)})


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


# ───────────── Catálogo ─────────────
class VariantIn(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    attributes: dict[str, str] = Field(default_factory=dict)
    gtin: str | None = Field(default=None, pattern=r"^\d{8,14}$")
    weight_g: int = Field(gt=0, le=100_000)
    length_mm: int = Field(gt=0, le=3000)
    width_mm: int = Field(gt=0, le=3000)
    height_mm: int = Field(gt=0, le=3000)


class MediaIn(BaseModel):
    url: str = Field(min_length=10, max_length=1000)
    alt: str = Field(min_length=3, max_length=200)


class ProductIn(BaseModel):
    sku_base: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=5000)
    category: str = Field(min_length=2, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    ncm: str | None = Field(default=None, pattern=r"^\d{8}$")
    regulated: bool = False
    regulatory_docs: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list, max_length=20)
    media: list[MediaIn] | None = Field(default=None, max_length=10)
    status: Literal["DRAFT", "ACTIVE"] = "ACTIVE"
    variants: list[VariantIn] = Field(min_length=1, max_length=50)


class ProductUpdateIn(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, min_length=2, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    ncm: str | None = Field(default=None, pattern=r"^\d{8}$")
    regulated: bool | None = None
    regulatory_docs: list[str] | None = None
    media: list[MediaIn] | None = Field(default=None, max_length=10)


class VariantUpdateIn(BaseModel):
    expected_version: int = Field(ge=1, description="versão do PRODUTO que o usuário viu")
    attributes: dict[str, str] | None = None
    gtin: str | None = Field(default=None, pattern=r"^\d{8,14}$")
    weight_g: int | None = Field(default=None, gt=0, le=100_000)
    length_mm: int | None = Field(default=None, gt=0, le=3000)
    width_mm: int | None = Field(default=None, gt=0, le=3000)
    height_mm: int | None = Field(default=None, gt=0, le=3000)
    status: Literal["ACTIVE", "INACTIVE"] | None = None


class StatusIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def variant_out(v: Variant) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "sku": v.sku,
        "attributes": v.attributes,
        "gtin": v.gtin,
        "weight_g": v.weight_g,
        "dims_mm": [v.length_mm, v.width_mm, v.height_mm],
        "status": v.status,
    }


def product_out(db: Session, p: Product) -> dict[str, Any]:
    vs = db.execute(select(Variant).where(Variant.product_id == p.id).order_by(Variant.sku)).scalars()
    return {
        "id": str(p.id),
        "sku_base": p.sku_base,
        "title": p.title,
        "description": p.description,
        "category": p.category,
        "brand": p.brand,
        "ncm": p.ncm,
        "regulated": p.regulated,
        "images": p.images,
        "media": p.media,
        "status": p.status,
        "version": p.version,
        "simulated": p.is_simulated,
        "variants": [variant_out(v) for v in vs],
    }


@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(body: ProductIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog:write"))):
    data = body.model_dump(exclude={"variants", "media"})
    if body.media is not None:
        try:
            media = catalog.validate_media([m.model_dump() for m in body.media])
        except catalog.CatalogError as e:
            raise _err(e) from e
        data["images"] = [m["url"] for m in media]
    else:  # legado: só URLs — texto alternativo provisório = título (revisar no catálogo)
        media = [{"url": u, "alt": body.title} for u in body.images]
    prod = Product(**data, media=media, is_simulated=get_settings().is_dry_run)
    db.add(prod)
    try:
        db.flush()
        for v in body.variants:
            db.add(Variant(product_id=prod.id, **v.model_dump()))
        db.flush()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "DUPLICATE_SKU", "message": "SKU já existe"}) from e
    catalog.record_version(db, prod, actor=str(p.user_id), changed=["created"])
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="catalog.product.created",
        subject_type="product",
        subject_id=str(prod.id),
        payload={"sku_base": prod.sku_base},
    )
    events.emit(
        db,
        "catalog.product.created",
        agent_id="nova",
        payload={"product_id": str(prod.id), "title": prod.title, "simulated": prod.is_simulated},
    )
    return product_out(db, prod)


@router.post("/media", status_code=status.HTTP_201_CREATED)
async def upload_media(request: Request, p: Principal = Depends(require("catalog:write"))):
    """Envia UMA foto (corpo = bytes da imagem; JPEG, PNG ou WebP até 10 MB). Volta a URL pública para usar em
    `media` do produto (PUT /products/{id}). A imagem é regravada sem metadados (GPS/EXIF) e com tamanho limitado."""
    from starlette.concurrency import run_in_threadpool

    from aicommerce.commerce import media
    from aicommerce.db import session_scope

    declared = int(request.headers.get("content-length") or 0)
    if declared > media.MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, {"code": "TOO_LARGE", "message": "máximo 10 MB"})
    data = bytearray()
    async for chunk in request.stream():  # corta no limite mesmo sem Content-Length confiável
        data += chunk
        if len(data) > media.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, {"code": "TOO_LARGE", "message": "máximo 10 MB"}
            )

    def _store() -> dict[str, Any]:
        with session_scope() as db:
            out = media.store(db, bytes(data), actor=str(p.user_id))
            audit.record(
                db,
                actor_type="user",
                actor_id=str(p.user_id),
                action="catalog.media.uploaded",
                subject_type="media",
                subject_id=str(out["sha256"]),
                payload={k: out[k] for k in ("width", "height", "bytes")},
            )
            return out

    try:
        return await run_in_threadpool(_store)
    except media.MediaError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "INVALID_IMAGE", "message": str(e)}) from e


@router.get("/media/{name}", include_in_schema=False)
def get_media(name: str, db: Session = Depends(get_db)):
    """Público (os canais baixam as fotos por aqui). Endereço = SHA-256 do conteúdo: imutável, cache eterno."""
    from fastapi.responses import Response

    from aicommerce.commerce import media

    blob = media.load(db, name)
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "imagem não encontrada")
    return Response(
        blob.data,
        media_type=blob.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{blob.sha256}"',
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/products/{product_id}")
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdateIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    """Edição com controle otimista de versão (expected_version). Anúncios afetados ganham PRODUCT_CHANGED."""
    changes = body.model_dump(exclude={"expected_version"}, exclude_none=True)
    if body.media is not None:
        changes["media"] = [m.model_dump() for m in body.media]
    try:
        prod, changed, stale = catalog.update(
            db, product_id, expected_version=body.expected_version, changes=changes, actor=str(p.user_id)
        )
    except catalog.CatalogError as e:
        raise _err(e) from e
    return {**product_out(db, prod), "changed": changed, "listings_marked": stale}


@router.put("/variants/{variant_id}")
def update_variant(
    variant_id: uuid.UUID,
    body: VariantUpdateIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    try:
        prod, _, changed = catalog.update_variant(
            db,
            variant_id,
            expected_version=body.expected_version,
            changes=body.model_dump(exclude={"expected_version"}, exclude_none=True),
            actor=str(p.user_id),
        )
    except catalog.CatalogError as e:
        raise _err(e) from e
    return {**product_out(db, prod), "changed": changed}


@router.post("/products/{product_id}/archive")
def archive_product(
    product_id: uuid.UUID,
    body: StatusIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    try:
        return product_out(
            db, catalog.set_status(db, product_id, archived=True, actor=str(p.user_id), reason=body.reason)
        )
    except catalog.CatalogError as e:
        raise _err(e) from e


@router.post("/products/{product_id}/reactivate")
def reactivate_product(
    product_id: uuid.UUID,
    body: StatusIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    try:
        return product_out(
            db, catalog.set_status(db, product_id, archived=False, actor=str(p.user_id), reason=body.reason)
        )
    except catalog.CatalogError as e:
        raise _err(e) from e


@router.get("/products/{product_id}/versions")
def product_versions(
    product_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))
):
    return {"items": catalog.history(db, product_id)}


@router.get("/products")
def list_products(db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    prods = db.execute(select(Product).order_by(Product.created_at.desc()).limit(200)).scalars()
    return {"items": [product_out(db, x) for x in prods]}


@router.get("/products/{product_id}")
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    prod = db.get(Product, product_id)
    if prod is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "produto inexistente")
    offers = db.execute(
        select(SupplierOffer).join(Variant, Variant.id == SupplierOffer.variant_id).where(Variant.product_id == prod.id)
    ).scalars()
    return {**product_out(db, prod), "offers": [offer_out(o) for o in offers]}


# ───────────── Fornecedores ─────────────
class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    legal_id: str | None = Field(default=None, max_length=32)
    origin: Literal["NATIONAL", "INTERNATIONAL"]
    country: str = Field(pattern=r"^[A-Z]{2}$")
    sla_dispatch_days: int | None = Field(default=None, ge=0, le=60)
    notes: str = Field(default="", max_length=2000)


def offer_out(o: SupplierOffer) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "supplier_id": str(o.supplier_id),
        "variant_id": str(o.variant_id),
        "cost_minor": o.cost_minor,
        "currency": o.currency,
        "moq": o.moq,
        "lead_time_days": o.lead_time_days,
        "stock_qty": o.stock_qty,
        "stock_observed_at": _iso(o.stock_observed_at),
        "valid_until": _iso(o.valid_until),
        "status": o.status,
        "source_ref": o.source_ref,
    }


def supplier_out(db: Session, s: Supplier, *, full: bool = False) -> dict[str, Any]:
    _, docs, offers = suppliers.load(db, s.id)
    a = suppliers.assess(s, docs, offers)
    out = {
        "id": str(s.id),
        "name": s.name,
        "legal_id": s.legal_id,
        "origin": s.origin,
        "country": s.country,
        "status": s.status,
        "sla_dispatch_days": s.sla_dispatch_days,
        "sample_status": s.sample_status,
        "version": s.version,
        "simulated": s.is_simulated,
        "assessment": a.as_dict(),
        "note": "Aprovação interna do catálogo; não substitui o gate SUP-01 de go-live.",
    }
    if full:
        out["documents"] = [
            {
                "id": str(d.id),
                "doc_type": d.doc_type,
                "reference": d.reference,
                "sha256": d.sha256,
                "verified": d.verified_at is not None,
                "verified_at": _iso(d.verified_at),
                "expires_at": _iso(d.expires_at),
            }
            for d in docs
        ]
        out["offers"] = [offer_out(o) for o in offers]
        out["notes"] = s.notes
    return out


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
def create_supplier(body: SupplierIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog:write"))):
    s = Supplier(**body.model_dump(), is_simulated=get_settings().is_dry_run)
    db.add(s)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="supplier.created",
        subject_type="supplier",
        subject_id=str(s.id),
        payload={"name": s.name, "origin": s.origin},
    )
    events.emit(
        db,
        "supplier.status_changed",
        agent_id="nexus",
        payload={"supplier_id": str(s.id), "name": s.name, "from": None, "to": s.status, "simulated": s.is_simulated},
    )
    return supplier_out(db, s, full=True)


@router.get("/suppliers")
def list_suppliers(db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    rows = db.execute(select(Supplier).order_by(Supplier.created_at.desc()).limit(200)).scalars()
    return {"items": [supplier_out(db, s) for s in rows]}


@router.get("/suppliers/{supplier_id}")
def get_supplier(
    supplier_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))
):
    s = db.get(Supplier, supplier_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fornecedor inexistente")
    return supplier_out(db, s, full=True)


class DocumentIn(BaseModel):
    doc_type: str = Field(max_length=32)
    reference: str = Field(min_length=3, max_length=1000, description="caminho/URL do documento guardado")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expires_at: dt.datetime | None = None


@router.post("/suppliers/{supplier_id}/documents", status_code=status.HTTP_201_CREATED)
def add_document(
    supplier_id: uuid.UUID,
    body: DocumentIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    if body.doc_type not in suppliers.DOC_TYPES:
        raise HTTPException(422, {"code": "DOC_TYPE", "message": f"tipos válidos: {sorted(suppliers.DOC_TYPES)}"})
    if db.get(Supplier, supplier_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fornecedor inexistente")
    d = SupplierDocument(
        supplier_id=supplier_id,
        doc_type=body.doc_type,
        reference=body.reference,
        sha256=body.sha256 or sha256_of(body.reference),
        expires_at=body.expires_at,
    )
    db.add(d)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="supplier.document.added",
        subject_type="supplier",
        subject_id=str(supplier_id),
        payload={"doc_type": d.doc_type, "sha256": d.sha256},
    )
    return {"id": str(d.id), "doc_type": d.doc_type, "sha256": d.sha256, "verified": False}


@router.post("/suppliers/{supplier_id}/documents/{document_id}/verify")
def verify_document(
    supplier_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("suppliers:approve")),
):
    d = db.get(SupplierDocument, document_id, with_for_update=True)
    if d is None or d.supplier_id != supplier_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "documento inexistente")
    if d.verified_at is None:
        d.verified_at, d.verified_by = dt.datetime.now(dt.UTC), str(p.user_id)
        audit.record(
            db,
            actor_type="user",
            actor_id=str(p.user_id),
            action="supplier.document.verified",
            subject_type="supplier",
            subject_id=str(supplier_id),
            payload={"document_id": str(d.id), "doc_type": d.doc_type, "sha256": d.sha256},
        )
    return supplier_out(db, db.get(Supplier, supplier_id), full=True)


class SampleIn(BaseModel):
    sample_status: Literal["NOT_REQUESTED", "REQUESTED", "RECEIVED", "APPROVED", "REJECTED"]
    expected_version: int


@router.post("/suppliers/{supplier_id}/sample")
def set_sample(
    supplier_id: uuid.UUID,
    body: SampleIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    s = db.get(Supplier, supplier_id, with_for_update=True)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fornecedor inexistente")
    if s.version != body.expected_version:
        raise _err(suppliers.VersionConflict(f"versão atual {s.version}"))
    if body.sample_status == "APPROVED" and not p.has("suppliers:approve"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "aprovar amostra exige suppliers:approve")
    before, s.sample_status, s.version = s.sample_status, body.sample_status, s.version + 1
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="supplier.sample",
        subject_type="supplier",
        subject_id=str(s.id),
        payload={"from": before, "to": s.sample_status},
    )
    return supplier_out(db, s, full=True)


class TransitionIn(BaseModel):
    to: Literal["PROSPECT", "EVALUATING", "APPROVED", "SUSPENDED", "REJECTED"]
    expected_version: int
    reason: str = Field(min_length=3, max_length=500)


@router.post("/suppliers/{supplier_id}/transition")
def transition_supplier(
    supplier_id: uuid.UUID,
    body: TransitionIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    if body.to in ("APPROVED", "SUSPENDED") and not p.mfa:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "aprovar/suspender fornecedor exige MFA")
    try:
        s = suppliers.transition(
            db,
            supplier_id,
            to=body.to,
            expected_version=body.expected_version,
            actor=str(p.user_id),
            reason=body.reason,
            can_approve=p.has("suppliers:approve"),
        )
    except suppliers.SupplierError as e:
        raise _err(e) from e
    return supplier_out(db, s, full=True)


class OfferIn(BaseModel):
    variant_id: uuid.UUID
    cost_minor: int = Field(gt=0)
    currency: Literal["BRL"] = "BRL"
    moq: int = Field(default=1, ge=1)
    lead_time_days: int = Field(ge=0, le=120)
    stock_qty: int = Field(ge=0)
    valid_until: dt.datetime | None = None
    source_ref: str = Field(min_length=3, max_length=500, description="origem/evidência da cotação")


@router.post("/suppliers/{supplier_id}/offers", status_code=status.HTTP_201_CREATED)
def create_offer(
    supplier_id: uuid.UUID,
    body: OfferIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    if db.get(Supplier, supplier_id) is None or db.get(Variant, body.variant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fornecedor ou variante inexistente")
    o = SupplierOffer(supplier_id=supplier_id, stock_observed_at=dt.datetime.now(dt.UTC), **body.model_dump())
    db.add(o)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="supplier.offer.created",
        subject_type="supplier_offer",
        subject_id=str(o.id),
        payload={"cost_minor": o.cost_minor, "stock_qty": o.stock_qty, "source_ref": o.source_ref},
    )
    return offer_out(o)


class StockIn(BaseModel):
    stock_qty: int = Field(ge=0)
    cost_minor: int | None = Field(default=None, gt=0)


@router.post("/offers/{offer_id}/stock")
def observe_stock(
    offer_id: uuid.UUID, body: StockIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog:write"))
):
    o = db.get(SupplierOffer, offer_id, with_for_update=True)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "oferta inexistente")
    before = {"stock_qty": o.stock_qty, "cost_minor": o.cost_minor}
    o.stock_qty, o.stock_observed_at = body.stock_qty, dt.datetime.now(dt.UTC)
    if body.cost_minor is not None:
        o.cost_minor = body.cost_minor
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="supplier.offer.stock_observed",
        subject_type="supplier_offer",
        subject_id=str(o.id),
        payload={"before": before, "stock_qty": o.stock_qty, "cost_minor": o.cost_minor},
    )
    return offer_out(o)


# ───────────── Preço e margem ─────────────
class QuoteIn(BaseModel):
    channel: Channel
    cost_minor: int = Field(gt=0)
    shipping_minor: int = Field(default=0, ge=0)
    ads_cpa_minor: int = Field(default=0, ge=0)
    returns_bps: int = Field(default=200, ge=0, le=5000)
    target_margin_bps: int = Field(default=1500, ge=0, le=9000)
    price_minor: int | None = Field(default=None, gt=0)


@router.post("/pricing/quote")
def quote(body: QuoteIn, db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    try:
        inp, params = listings.pricing_input(
            db,
            channel=body.channel,
            cost_minor=body.cost_minor,
            shipping_minor=body.shipping_minor,
            ads_cpa_minor=body.ads_cpa_minor,
            returns_bps=body.returns_bps,
            target_margin_bps=body.target_margin_bps,
        )
    except listings.ListingError as e:
        raise _err(e) from e
    q = pricing.quote(inp, body.price_minor)
    return {**q.as_dict(), "params": params, "simulated": get_settings().is_dry_run}


# ───────────── Anúncios ─────────────
class PrepareIn(BaseModel):
    variant_id: uuid.UUID
    offer_id: uuid.UUID
    fulfillment_mode: Literal["DROPSHIP_SUPPLIER", "OWN_STOCK", "CROSS_BORDER"] = "DROPSHIP_SUPPLIER"
    uf: str = Field(default="SP", pattern=r"^[A-Za-z]{2}$")
    price_minor: int | None = Field(default=None, gt=0, description="vazio = preço sugerido para a margem-alvo")
    target_margin_bps: int = Field(default=1500, ge=0, le=9000)
    shipping_minor: int = Field(default=0, ge=0)
    ads_cpa_minor: int = Field(default=0, ge=0)
    returns_bps: int = Field(default=200, ge=0, le=5000)


def listing_out(db: Session, x: ChannelListing) -> dict[str, Any]:
    v = db.get(Variant, x.variant_id)
    prod = db.get(Product, v.product_id) if v else None
    return {
        "id": str(x.id),
        "channel": x.channel,
        "status": x.status,
        "version": x.version,
        "variant_id": str(x.variant_id),
        "sku": v.sku if v else None,
        "title": prod.title if prod else None,
        "offer_id": str(x.offer_id),
        "price_minor": x.price_minor,
        "currency": x.currency,
        "fulfillment_mode": x.fulfillment_mode,
        "uf": x.uf,
        "pricing": x.pricing,
        "compliance": x.compliance,
        "blockers": [i for i in x.compliance if i["severity"] == "BLOCKER"],
        "channel_payload": x.channel_payload,
        "external_id": x.external_id,
        "proposal_id": str(x.proposal_id) if x.proposal_id else None,
        "simulated": x.is_simulated,
        "updated_at": _iso(x.updated_at),
    }


@router.post("/channels/{channel}/listings/prepare")
def prepare_listing(
    channel: Channel,
    body: PrepareIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("listings:prepare")),
):
    try:
        x = listings.prepare(db, channel=channel, actor=str(p.user_id), **body.model_dump())
    except listings.ListingError as e:
        raise _err(e) from e
    return listing_out(db, x)


@router.get("/listings")
def list_listings(
    channel: Channel | None = None, db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))
):
    q = select(ChannelListing).order_by(ChannelListing.updated_at.desc()).limit(200)
    if channel:
        q = q.where(ChannelListing.channel == channel)
    return {"items": [listing_out(db, x) for x in db.execute(q).scalars()]}


@router.get("/listings/{listing_id}")
def get_listing(listing_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    x = db.get(ChannelListing, listing_id)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "anúncio inexistente")
    versions = db.execute(
        select(ListingVersion).where(ListingVersion.listing_id == x.id).order_by(ListingVersion.version)
    ).scalars()
    return {
        **listing_out(db, x),
        "versions": [
            {"version": v.version, "payload_sha256": v.payload_sha256, "created_at": _iso(v.created_at)}
            for v in versions
        ],
    }


class PauseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/listings/{listing_id}/propose-pause", status_code=status.HTTP_201_CREATED)
def propose_pause(
    listing_id: uuid.UUID,
    body: PauseIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("proposals:create")),
    rid: str = Depends(request_id),
):
    try:
        prop = listings.propose_pause(
            db, listing_id, actor=str(p.user_id), reason=body.reason, request_id=rid, nonce=uuid.uuid4().hex[:12]
        )
    except listings.ListingError as e:
        raise _err(e) from e
    return proposal_out(prop)


@router.post("/listings/{listing_id}/propose-publish", status_code=status.HTTP_201_CREATED)
def propose_publish(
    listing_id: uuid.UUID,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("listings:prepare")),
    rid: str = Depends(request_id),
):
    if not p.has("proposals:create"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "permissão necessária: proposals:create")
    try:
        prop = listings.propose_publish(db, listing_id, actor=str(p.user_id), request_id=rid)
    except listings.ListingError as e:
        raise _err(e) from e
    return proposal_out(prop)


# ───────────── Webhooks ─────────────
@router.post("/webhooks/woocommerce", status_code=status.HTTP_200_OK)
async def woocommerce_webhook(
    request: Request,
    x_wc_webhook_signature: str | None = Header(default=None),
    x_wc_webhook_delivery_id: str | None = Header(default=None),
    x_wc_webhook_topic: str | None = Header(default=None),
    x_wc_webhook_resource: str | None = Header(default=None),
):
    raw = await request.body()
    if raw.startswith(b"webhook_id="):  # ping de criação do webhook (form-urlencoded, sem assinatura)
        return {"ack": True, "ping": True}
    if not verify_webhook_signature(get_settings().woo_webhook_secret, raw, x_wc_webhook_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "assinatura inválida")
    if not x_wc_webhook_delivery_id or not x_wc_webhook_topic:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cabeçalhos X-WC-Webhook-Delivery-ID/Topic ausentes")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corpo não é JSON") from e
    from starlette.concurrency import run_in_threadpool

    def _persist() -> bool:
        from aicommerce.db import session_scope

        with session_scope() as db:
            return webhooks.ingest(
                db,
                provider="woocommerce",
                delivery_id=x_wc_webhook_delivery_id,
                topic=x_wc_webhook_topic,
                resource_id=str(payload.get("id", "")) or None,
                payload=payload if isinstance(payload, dict) else {"data": payload},
            )

    new = await run_in_threadpool(_persist)
    return {"ack": True, "duplicate": not new, "resource": x_wc_webhook_resource}


# ───────────── Contas de canal, categorias e Mercado Livre ─────────────
@router.get("/channels/accounts")
def list_accounts(db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    from aicommerce.models import ChannelAccount

    rows = db.execute(select(ChannelAccount).order_by(ChannelAccount.channel)).scalars()
    return {
        "items": [
            {
                "id": str(a.id),
                "channel": a.channel,
                "external_account_id": a.external_account_id,
                "status": a.status,
                "expires_at": _iso(a.expires_at),
                "simulated": a.is_simulated,
            }
            for a in rows
        ]
    }  # tokens nunca saem da API


@router.post("/sim/channels/{channel}/connect", status_code=status.HTTP_201_CREATED)
def sim_connect(
    channel: Literal["MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"],
    db: Session = Depends(get_db),
    p: Principal = Depends(require("sim:control")),
):
    from aicommerce.commerce import channels

    if not get_settings().is_dry_run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "simulação indisponível fora de DRY_RUN")
    a = channels.connect_simulated(db, channel, actor=str(p.user_id))
    return {
        "channel": a.channel,
        "external_account_id": a.external_account_id,
        "status": a.status,
        "expires_at": _iso(a.expires_at),
        "simulated": True,
    }


@router.post("/channels/{channel}/oauth/start")
def oauth_start(
    channel: Literal["MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"],
    db: Session = Depends(get_db),
    p: Principal = Depends(require("gates:manage")),
):
    """Conectar a conta REAL do canal (LIVE): devolve a URL de autorização do canal. Só o proprietário com MFA.
    O verificador PKCE fica no servidor; o navegador só leva o state (uso único, 10 min)."""
    from aicommerce.commerce import channels

    if not p.mfa:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "MFA_REQUIRED", "message": "conectar conta exige MFA"})
    try:
        url = channels.oauth_start(db, channel, actor=str(p.user_id))
    except channels.OAuthError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "OAUTH_UNAVAILABLE", "message": str(e)}) from e
    return {"authorize_url": url, "expires_in_s": int(channels.OAUTH_TTL.total_seconds())}


def _oauth_page(title: str, msg: str, ok: bool) -> HTMLResponse:
    from html import escape

    color = "#1f9d55" if ok else "#c0392b"
    body = (
        "<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>AI Commerce</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:15vh auto;padding:0 1rem'>"
        f"<h1 style='color:{color}'>{escape(title)}</h1><p>{escape(msg)}</p></body></html>"
    )
    return HTMLResponse(body, status_code=200 if ok else 400, headers={"Cache-Control": "no-store"})


@router.get("/channels/MERCADO_LIVRE/oauth/callback", include_in_schema=False)
def ml_oauth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None, db: Session = Depends(get_db)
):
    """Redirecionamento do Mercado Livre após o proprietário autorizar. O state autentica o fluxo (uso único)."""
    from aicommerce.commerce import channels

    if error or not code or not state:
        return _oauth_page("Conexão não concluída", f"O Mercado Livre não autorizou ({error or 'sem código'}).", False)
    try:
        acc = channels.oauth_finish(db, code=code[:512], state=state[:128])
    except channels.OAuthError as e:
        db.commit()  # o state é consumido mesmo quando a troca falha (uso único)
        return _oauth_page("Conexão não concluída", str(e), False)
    return _oauth_page(
        "Conta Mercado Livre conectada",
        f"Vendedor {acc.external_account_id} conectado. Pode fechar esta página e voltar ao app. "
        "Nada é publicado sem a homologação CH-ML aprovada e a sua aprovação de cada anúncio.",
        True,
    )


class CategoryMapIn(BaseModel):
    internal_category: str = Field(min_length=2, max_length=120)
    external_category_id: str = Field(min_length=1, max_length=64)
    verified: bool = False


@router.put("/channels/{channel}/categories")
def map_category(
    channel: Literal["MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"],
    body: CategoryMapIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("catalog:write")),
):
    from aicommerce.models import ChannelCategoryMap

    row = db.execute(
        select(ChannelCategoryMap).where(
            ChannelCategoryMap.channel == channel, ChannelCategoryMap.internal_category == body.internal_category
        )
    ).scalar_one_or_none()
    if row is None:
        row = ChannelCategoryMap(
            channel=channel,
            internal_category=body.internal_category,
            external_category_id=body.external_category_id,
            verified=body.verified,
            created_by=str(p.user_id),
        )
        db.add(row)
    else:
        row.external_category_id, row.verified = body.external_category_id, body.verified
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="channel.category.mapped",
        subject_type="channel",
        subject_id=channel,
        payload=body.model_dump(),
    )
    return {"channel": channel, **body.model_dump()}


@router.post("/webhooks/mercadolivre", status_code=status.HTTP_200_OK)
async def mercadolivre_webhook(request: Request):
    """ACK rápido após persistir. ML não assina o corpo: valida application_id, tópico e formato; dedupe."""
    from starlette.concurrency import run_in_threadpool

    from aicommerce.providers.marketplaces import ml_delivery_id, validate_ml_notification

    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corpo não é JSON") from e
    reason = validate_ml_notification(body, get_settings().ml_application_id)
    if reason:
        raise HTTPException(status.HTTP_403_FORBIDDEN if "application_id" in reason else 400, reason)

    def _persist() -> bool:
        from aicommerce.db import session_scope

        with session_scope() as db:
            return webhooks.ingest(
                db,
                provider="mercadolivre",
                delivery_id=ml_delivery_id(body),
                topic=body["topic"],
                resource_id=str(body["resource"]),
                payload=body,
            )

    new = await run_in_threadpool(_persist)
    return {"ack": True, "duplicate": not new}


class SimMLOrderIn(BaseModel):
    listing_id: uuid.UUID
    qty: int = Field(default=1, ge=1, le=50)


@router.post("/sim/mercadolivre/orders", status_code=status.HTTP_201_CREATED)
def sim_ml_order(body: SimMLOrderIn, db: Session = Depends(get_db), p: Principal = Depends(require("sim:control"))):
    """DRY_RUN: cria o pedido na 'loja' simulada do ML e envia a notificação pelo mesmo caminho do canal real."""
    from aicommerce.models import SimChannelResource

    if not get_settings().is_dry_run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "simulação indisponível fora de DRY_RUN")
    x = db.get(ChannelListing, body.listing_id)
    if x is None or x.channel != "MERCADO_LIVRE" or x.status != "PUBLISHED":
        raise HTTPException(409, {"code": "LISTING_NOT_PUBLISHED", "message": "anúncio ML publicado é necessário"})
    v = db.get(Variant, x.variant_id)
    oid = int(uuid.uuid4().int % 10**10)
    now = dt.datetime.now(dt.UTC).isoformat()
    unit = x.price_minor / 100
    order = {
        "id": oid,
        "status": "paid",
        "date_created": now,
        "last_updated": now,
        "currency_id": "BRL",
        "total_amount": round(unit * body.qty, 2),
        "order_items": [{"item": {"id": x.external_id, "seller_sku": v.sku}, "quantity": body.qty, "unit_price": unit}],
        "payments": [
            {
                "id": oid + 1,
                "status": "approved",
                "total_paid_amount": round(unit * body.qty, 2),
                "payment_method_id": "pix",
            }
        ],
        "buyer": {"id": 1, "nickname": "COMPRADOR_SIMULADO"},
        "shipping": {"state": x.uf},
    }
    resource = f"/orders/{oid}"
    db.add(SimChannelResource(channel="MERCADO_LIVRE", resource=resource, payload=order))
    note = {
        "_id": f"sim-{uuid.uuid4().hex[:12]}",
        "resource": resource,
        "user_id": 1,
        "topic": "orders_v2",
        "application_id": get_settings().ml_application_id,
        "attempts": 1,
        "sent": now,
        "received": now,
    }
    webhooks.ingest(
        db, provider="mercadolivre", delivery_id=note["_id"], topic="orders_v2", resource_id=resource, payload=note
    )
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="sim.ml_order",
        subject_type="listing",
        subject_id=str(x.id),
        payload={"resource": resource},
    )
    return {"resource": resource, "notification_id": note["_id"]}
