"""Pedidos, envios, devoluções, notas, repasses, conciliação e alertas (COM-009..012; FR-021).

Rotas /v1/sim/* existem só em DRY_RUN: geram fatos simulados (pedido de cliente fictício, atualização de
transportadora, extrato de repasse) pelos MESMOS caminhos de ingestão usados pelos canais reais.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.api.deps import request_id, require
from aicommerce.api.routes_governance import proposal_out
from aicommerce.commerce import alerts, orders, settlements
from aicommerce.config import get_settings
from aicommerce.db import get_db
from aicommerce.governance import audit
from aicommerce.identity.security import Principal
from aicommerce.models import (
    ChannelListing,
    FiscalDocument,
    Fulfillment,
    Order,
    OrderEvent,
    OrderItem,
    Payment,
    ReturnRequest,
    Settlement,
    Variant,
)
from aicommerce.providers.fiscal import SIM_ENVIRONMENT

router = APIRouter(prefix="/v1", tags=["orders"])


def _err(e: Exception) -> HTTPException:
    return HTTPException(getattr(e, "http", 409), {"code": getattr(e, "code", "ERROR"), "message": str(e)})


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


def order_out(db: Session, o: Order, *, full: bool = False) -> dict[str, Any]:
    out = {
        "id": str(o.id),
        "channel": o.channel,
        "external_id": o.external_id,
        "status": o.status,
        "payment_status": o.payment_status,
        "fiscal_status": o.fiscal_status,
        "total_minor": o.total_minor,
        "items_total_minor": o.items_total_minor,
        "shipping_minor": o.shipping_minor,
        "currency": o.currency,
        "uf": o.uf,
        "customer_ref": o.customer_ref,
        "placed_at": _iso(o.placed_at),
        "delivered_at": _iso(o.delivered_at),
        "incident_reason": o.incident_reason,
        "simulated": o.is_simulated,
        "version": o.version,
    }
    if full:
        out["items"] = [
            {
                "sku": i.sku,
                "qty": i.qty,
                "unit_price_minor": i.unit_price_minor,
                "unit_cost_minor": i.unit_cost_minor,
                "stock_reserved": i.stock_reserved,
            }
            for i in db.execute(select(OrderItem).where(OrderItem.order_id == o.id)).scalars()
        ]
        out["events"] = [
            {"from": e.from_status, "to": e.to_status, "source": e.source, "at": _iso(e.occurred_at)}
            for e in db.execute(select(OrderEvent).where(OrderEvent.order_id == o.id).order_by(OrderEvent.id)).scalars()
        ]
        out["payments"] = [
            {"provider": p.provider, "status": p.status, "amount_minor": p.amount_minor, "method": p.method}
            for p in db.execute(select(Payment).where(Payment.order_id == o.id)).scalars()
        ]
        out["fulfillments"] = [
            {
                "id": str(f.id),
                "status": f.status,
                "supplier_id": str(f.supplier_id),
                "cost_minor": f.cost_minor,
                "carrier": f.carrier,
                "tracking_code": f.tracking_code,
                "promised_by": _iso(f.promised_by),
                "proposal_id": str(f.proposal_id) if f.proposal_id else None,
            }
            for f in db.execute(select(Fulfillment).where(Fulfillment.order_id == o.id)).scalars()
        ]
        # Chave de acesso mascarada: o documento completo exige fiscal:read (GET /orders/{id}/fiscal-documents).
        out["fiscal_documents"] = [
            {
                "id": str(d.id),
                "kind": d.kind,
                "status": d.status,
                "environment": d.environment,
                "access_key": mask_key(d.access_key),
                "reason": d.reason,
            }
            for d in db.execute(select(FiscalDocument).where(FiscalDocument.order_id == o.id)).scalars()
        ]
        out["returns"] = [
            {
                "id": str(r.id),
                "reason": r.reason,
                "status": r.status,
                "withdrawal_right": r.withdrawal_right,
                "refund_minor": r.refund_minor,
                "proposal_id": str(r.proposal_id) if r.proposal_id else None,
            }
            for r in db.execute(select(ReturnRequest).where(ReturnRequest.order_id == o.id)).scalars()
        ]
    return out


def _order(db: Session, order_id: uuid.UUID, lock: bool = False) -> Order:
    o = db.get(Order, order_id, with_for_update=lock)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pedido inexistente")
    return o


@router.get("/orders")
def list_orders(
    status_: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(require("orders:read"))
):
    q = select(Order).order_by(Order.placed_at.desc()).limit(200)
    if status_:
        q = q.where(Order.status == status_)
    return {"items": [order_out(db, o) for o in db.execute(q).scalars()]}


@router.get("/orders/{order_id}")
def get_order(order_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("orders:read"))):
    o = _order(db, order_id)
    return {
        **order_out(db, o, full=True),
        "customer": orders.customer_view(db, o, can_read_pii=False, actor=str(p.user_id)),
    }


@router.get("/orders/{order_id}/customer")
def get_customer(order_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("orders:pii"))):
    """Dados pessoais completos — acesso registrado na auditoria."""
    return orders.customer_view(db, _order(db, order_id), can_read_pii=True, actor=str(p.user_id))


def mask_key(key: str | None) -> str | None:
    return None if not key else f"{'•' * 4}{key[-4:]}"


@router.get("/orders/{order_id}/fiscal-documents")
def fiscal_documents(
    order_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("fiscal:read"))
):
    """Documentos fiscais completos do pedido (a NF-e identifica o destinatário) — acesso registrado na auditoria."""
    o = _order(db, order_id)
    docs = db.execute(select(FiscalDocument).where(FiscalDocument.order_id == o.id)).scalars().all()
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="fiscal.documents.viewed",
        subject_type="order",
        subject_id=str(o.id),
        payload={"documents": [str(d.id) for d in docs]},
    )
    return {
        "order_id": str(o.id),
        "items": [
            {
                "id": str(d.id),
                "kind": d.kind,
                "status": d.status,
                "environment": d.environment,
                "access_key": d.access_key,
                "provider": d.provider,
                "reason": d.reason,
                "issued_at": _iso(d.issued_at),
                "simulated": d.environment == SIM_ENVIRONMENT,
            }
            for d in docs
        ],
    }


@router.post("/orders/{order_id}/fulfill", status_code=status.HTTP_201_CREATED)
def fulfill(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("orders:manage")),
    rid: str = Depends(request_id),
):
    try:
        props = orders.request_fulfillment(db, _order(db, order_id, lock=True), actor=str(p.user_id), request_id=rid)
    except orders.OrderError as e:
        raise _err(e) from e
    return {"proposals": [proposal_out(x) for x in props]}


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/orders/{order_id}/cancel")
def cancel(
    order_id: uuid.UUID, body: CancelIn, db: Session = Depends(get_db), p: Principal = Depends(require("orders:manage"))
):
    o = _order(db, order_id, lock=True)
    try:
        orders.cancel(db, o, reason=body.reason, source=str(p.user_id))
    except orders.OrderError as e:
        raise _err(e) from e
    return order_out(db, o, full=True)


class ReturnIn(BaseModel):
    reason: Literal["ARREPENDIMENTO", "DEFEITO", "NAO_RECEBIDO", "DIVERGENTE", "OUTRO"]
    description: str = Field(default="", max_length=2000)


@router.post("/orders/{order_id}/returns", status_code=status.HTTP_201_CREATED)
def open_return(
    order_id: uuid.UUID,
    body: ReturnIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("orders:manage")),
    rid: str = Depends(request_id),
):
    o = _order(db, order_id, lock=True)
    try:
        ret, prop = orders.request_return(
            db, o, reason=body.reason, description=body.description, actor=str(p.user_id), request_id=rid
        )
    except orders.OrderError as e:
        raise _err(e) from e
    return {
        "return_id": str(ret.id),
        "withdrawal_right": ret.withdrawal_right,
        "refund_minor": ret.refund_minor,
        "proposal": proposal_out(prop),
    }


class ShippingIn(BaseModel):
    status: Literal["SHIPPED", "DELIVERED"]
    carrier: str | None = Field(default=None, max_length=64)
    tracking_code: str | None = Field(default=None, max_length=64)


@router.post("/fulfillments/{fulfillment_id}/tracking")
def tracking(
    fulfillment_id: uuid.UUID,
    body: ShippingIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("orders:manage")),
):
    f = db.get(Fulfillment, fulfillment_id, with_for_update=True)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "envio inexistente")
    if body.status == "SHIPPED" and not body.tracking_code:
        raise HTTPException(422, {"code": "TRACKING_REQUIRED", "message": "código de rastreio obrigatório"})
    try:
        orders.update_shipping(
            db, f, status=body.status, carrier=body.carrier, tracking_code=body.tracking_code, source=str(p.user_id)
        )
    except orders.OrderError as e:
        raise _err(e) from e
    return order_out(db, _order(db, f.order_id), full=True)


# ───────────── Repasses, conciliação e alertas ─────────────
class SettlementLineIn(BaseModel):
    order_external_id: str = Field(max_length=64)
    gross_minor: int = Field(ge=0, description="valor positivo; o sentido vem de kind")
    fee_minor: int = Field(description="tarifa cobrada; negativa quando o canal devolve comissão")
    kind: Literal["SALE", "REFUND", "CHARGEBACK", "CHARGEBACK_REVERSAL"] = "SALE"


class SettlementIn(BaseModel):
    channel: Literal["WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"]
    external_id: str = Field(min_length=1, max_length=64)
    period_start: dt.datetime
    period_end: dt.datetime
    lines: list[SettlementLineIn] = Field(min_length=1, max_length=5000)


@router.post("/finance/settlements/import", status_code=status.HTTP_201_CREATED)
def import_settlement(
    body: SettlementIn, db: Session = Depends(get_db), p: Principal = Depends(require("finance:reconcile"))
):
    try:
        s, created = settlements.import_settlement(
            db,
            channel=body.channel,
            external_id=body.external_id,
            period_start=body.period_start,
            period_end=body.period_end,
            lines=[x.model_dump() for x in body.lines],
            actor=str(p.user_id),
            simulated=get_settings().is_dry_run,
        )
    except settlements.SettlementError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": str(s.id), "net_minor": s.net_minor, "replayed": not created}


@router.get("/finance/settlements")
def list_settlements(db: Session = Depends(get_db), _: Principal = Depends(require("finance:read"))):
    rows = db.execute(select(Settlement).order_by(Settlement.imported_at.desc()).limit(100)).scalars()
    return {
        "items": [
            {
                "id": str(s.id),
                "channel": s.channel,
                "external_id": s.external_id,
                "gross_minor": s.gross_minor,
                "fees_minor": s.fees_minor,
                "net_minor": s.net_minor,
                "simulated": s.is_simulated,
                "imported_at": _iso(s.imported_at),
            }
            for s in rows
        ]
    }


@router.get("/finance/reconciliation")
def reconciliation(
    channel: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(require("finance:read"))
):
    return settlements.reconcile(db, channel)


@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db), _: Principal = Depends(require("office:read"))):
    items = alerts.compute(db)
    return {"items": items, "critical": sum(a["severity"] == "CRITICAL" for a in items)}


# ───────────── Simulação (somente DRY_RUN) ─────────────
def _dry_run_only() -> None:
    if not get_settings().is_dry_run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "simulação indisponível fora de DRY_RUN")


class SimOrderIn(BaseModel):
    listing_id: uuid.UUID
    qty: int = Field(default=1, ge=1, le=100)
    paid: bool = True
    shipping_minor: int = Field(default=1500, ge=0)


@router.post("/sim/orders", status_code=status.HTTP_201_CREATED, dependencies=[Depends(_dry_run_only)])
def sim_order(body: SimOrderIn, db: Session = Depends(get_db), p: Principal = Depends(require("sim:control"))):
    listing = db.get(ChannelListing, body.listing_id)
    if listing is None or listing.status != "PUBLISHED":
        raise HTTPException(409, {"code": "LISTING_NOT_PUBLISHED", "message": "anúncio precisa estar publicado"})
    v = db.get(Variant, listing.variant_id)
    ext = f"SIM-{uuid.uuid4().hex[:8].upper()}"
    now = dt.datetime.now(dt.UTC)
    o, _ = orders.ingest(
        db,
        channel=listing.channel,
        external_id=ext,
        items=[{"sku": v.sku, "qty": body.qty, "unit_price_minor": listing.price_minor, "listing_id": str(listing.id)}],
        shipping_minor=body.shipping_minor,
        discount_minor=0,
        customer={
            "name": "Cliente Simulado",
            "email": f"{ext.lower()}@cliente.simulado.invalid",
            "phone": "+55 11 90000-0000",
            "address": "Rua Fictícia, 100",
            "city": "São Paulo",
            "uf": listing.uf,
        },
        placed_at=now,
        external_updated_at=now,
        payment={
            "provider": "sim-gateway",
            "external_id": f"PAY-{ext}",
            "method": "pix",
            "amount_minor": listing.price_minor * body.qty + body.shipping_minor,
        }
        if body.paid
        else None,
        cancelled=False,
        source=f"sim:{p.user_id}",
    )
    return order_out(db, o, full=True)


@router.post("/sim/fulfillments/{fulfillment_id}/{event}", dependencies=[Depends(_dry_run_only)])
def sim_shipping(
    fulfillment_id: uuid.UUID,
    event: Literal["ship", "deliver"],
    db: Session = Depends(get_db),
    p: Principal = Depends(require("sim:control")),
):
    f = db.get(Fulfillment, fulfillment_id, with_for_update=True)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "envio inexistente")
    try:
        if event == "ship":
            orders.update_shipping(
                db,
                f,
                status="SHIPPED",
                carrier="Transportadora Simulada",
                tracking_code=f"SIM{uuid.uuid4().hex[:10].upper()}BR",
                source="sim:carrier",
            )
        else:
            orders.update_shipping(db, f, status="DELIVERED", carrier=None, tracking_code=None, source="sim:carrier")
    except orders.OrderError as e:
        raise _err(e) from e
    return order_out(db, db.get(Order, f.order_id), full=True)


class SimSettlementIn(BaseModel):
    channel: Literal["WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"]
    fee_error_minor: int = Field(default=0, ge=0, le=100_000, description="diferença injetada para testar divergência")


@router.post("/sim/settlements", status_code=status.HTTP_201_CREATED, dependencies=[Depends(_dry_run_only)])
def sim_settlement(
    body: SimSettlementIn, db: Session = Depends(get_db), p: Principal = Depends(require("sim:control"))
):
    try:
        s = settlements.simulate_settlement(
            db, body.channel, actor=str(p.user_id), fee_error_minor=body.fee_error_minor
        )
    except settlements.SettlementError as e:
        raise _err(e) from e
    return {"id": str(s.id), "net_minor": s.net_minor, "gross_minor": s.gross_minor, "fees_minor": s.fees_minor}


@router.get("/dashboard/sales")
def dashboard_sales(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("dashboard:read")),
):
    from aicommerce.commerce import dashboard

    return dashboard.sales(db, days)
