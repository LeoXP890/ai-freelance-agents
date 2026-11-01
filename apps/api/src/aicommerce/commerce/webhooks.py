"""Webhooks de canais (SDD §5: ACK rápido após persistência durável; processa na fila;
suporta eventos repetidos e fora de ordem).

Recebimento: valida assinatura → INSERT … ON CONFLICT DO NOTHING por (provider, delivery_id) → 200.
Processamento (worker): aplica somente se o evento for mais novo que o último visto para o recurso.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicommerce.db import session_scope
from aicommerce.governance import audit
from aicommerce.models import ChannelListing, WebhookEvent
from aicommerce.office import events

WOO_STATUS = {"publish": "PUBLISHED", "draft": "PAUSED", "pending": "PAUSED", "private": "PAUSED"}


def ingest(
    db: Session, *, provider: str, delivery_id: str, topic: str, resource_id: str | None, payload: dict[str, Any]
) -> bool:
    """Persiste o evento. Retorna False se for entrega repetida (já recebida)."""
    stmt = insert(WebhookEvent).values(
        provider=provider,
        delivery_id=delivery_id,
        topic=topic,
        resource_id=resource_id,
        payload=payload,
        status="RECEIVED",
    )
    inserted = db.execute(
        stmt.on_conflict_do_nothing(index_elements=["provider", "delivery_id"]).returning(WebhookEvent.id)
    ).scalar_one_or_none()
    if inserted is not None:
        events.emit(
            db,
            "webhook.received",
            agent_id="nova",
            payload={"provider": provider, "topic": topic, "webhook_event_id": inserted},
        )
    return inserted is not None


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    ts = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.UTC)


def _meta(payload: dict[str, Any], key: str) -> Any:
    for m in payload.get("meta_data") or []:
        if isinstance(m, dict) and m.get("key") == key:
            return m.get("value")
    return None


def _apply_woo_product(db: Session, ev: WebhookEvent) -> tuple[str, str]:
    p = ev.payload
    listing = None
    listing_id = _meta(p, "aic_listing_id")
    try:
        lid = uuid.UUID(str(listing_id)) if listing_id else None
    except ValueError:
        lid = None
    if lid:
        listing = db.get(ChannelListing, lid, with_for_update=True)
    if listing is None and p.get("id") is not None:
        listing = db.execute(
            select(ChannelListing)
            .where(ChannelListing.channel == "WOOCOMMERCE", ChannelListing.external_id == str(p["id"]))
            .with_for_update()
        ).scalar_one_or_none()
    if listing is None:
        return "IGNORED", "produto não corresponde a nenhum anúncio conhecido"
    modified = _parse_ts(p.get("date_modified_gmt"))
    if modified is None:
        return "REJECTED", "evento sem date_modified_gmt: ordem não determinável"
    if listing.external_modified_at and modified <= listing.external_modified_at:
        return "IGNORED_STALE", f"evento de {modified.isoformat()} ≤ último aplicado"
    new_status = "DELETED_EXTERNALLY" if ev.topic == "product.deleted" else WOO_STATUS.get(p.get("status"), None)
    if new_status is None:
        return "REJECTED", f"status externo desconhecido: {p.get('status')}"
    before = listing.status
    listing.status, listing.external_modified_at = new_status, modified
    audit.record(
        db,
        actor_type="system",
        actor_id="webhook:woocommerce",
        action="listing.external_status",
        subject_type="listing",
        subject_id=str(listing.id),
        payload={"from": before, "to": new_status, "delivery_id": ev.delivery_id},
    )
    return "APPLIED", f"{before} → {new_status}"


def _minor(value: Any) -> int:
    return int((Decimal(str(value or "0")) * 100).quantize(Decimal("1")))


WOO_PAID = {"processing", "completed"}


def _apply_woo_order(db: Session, ev: WebhookEvent) -> tuple[str, str]:
    """Woo REST v3 Order → pedido canônico. Idempotente e ordenado por date_modified_gmt."""
    from aicommerce.commerce import orders

    p = ev.payload
    if p.get("id") is None:
        return "REJECTED", "pedido sem id"
    modified = _parse_ts(p.get("date_modified_gmt"))
    billing = p.get("billing") or {}
    shipping = p.get("shipping") or {}
    customer = {
        "name": " ".join(x for x in (billing.get("first_name"), billing.get("last_name")) if x),
        "email": billing.get("email"),
        "phone": billing.get("phone"),
        "address": shipping.get("address_1") or billing.get("address_1"),
        "postcode": shipping.get("postcode") or billing.get("postcode"),
        "city": shipping.get("city") or billing.get("city"),
        "uf": shipping.get("state") or billing.get("state"),
    }
    items = [
        {"sku": li.get("sku") or "", "qty": int(li.get("quantity", 0)), "unit_price_minor": _minor(li.get("price"))}
        for li in p.get("line_items") or []
    ]
    if not items or any(not i["sku"] or i["qty"] <= 0 for i in items):
        return "REJECTED", "itens sem SKU/quantidade"
    paid = p.get("status") in WOO_PAID and p.get("date_paid_gmt")
    payment = (
        {
            "provider": "woocommerce",
            "external_id": str(p.get("transaction_id") or f"woo-order-{p['id']}"),
            "method": p.get("payment_method") or "desconhecido",
            "amount_minor": _minor(p.get("total")),
            "captured_at": _parse_ts(p.get("date_paid_gmt")),
        }
        if paid
        else None
    )
    try:
        order, outcome = orders.ingest(
            db,
            channel="WOOCOMMERCE",
            external_id=str(p["id"]),
            items=items,
            shipping_minor=_minor(p.get("shipping_total")),
            discount_minor=_minor(p.get("discount_total")),
            customer=customer,
            placed_at=_parse_ts(p.get("date_created_gmt")) or dt.datetime.now(dt.UTC),
            external_updated_at=modified,
            payment=payment,
            cancelled=p.get("status") == "cancelled",
            source="webhook:woocommerce",
        )
    except orders.OrderError as e:
        return "REJECTED", str(e)
    return ("IGNORED_STALE" if outcome == "STALE" else "APPLIED"), f"{outcome}: pedido {order.status}"


ML_PAID = {"paid", "confirmed"}


def _apply_ml_order(db: Session, ev: WebhookEvent) -> tuple[str, str]:
    """Notificação ML traz só o caminho do recurso: busca o pedido (DRY_RUN: loja simulada) e aplica.
    Campos do pedido ML seguem conhecimento prévio NÃO conferido (ver providers/marketplaces.py)."""
    from aicommerce.commerce import orders
    from aicommerce.config import get_settings
    from aicommerce.models import SimChannelResource

    resource = ev.payload.get("resource", "")
    if not get_settings().is_dry_run:
        return "DEFERRED", "busca do recurso no ML real indisponível (gate CH-ML não homologado)"
    sim = db.execute(
        select(SimChannelResource).where(
            SimChannelResource.channel == "MERCADO_LIVRE", SimChannelResource.resource == resource
        )
    ).scalar_one_or_none()
    if sim is None:
        return "REJECTED", f"recurso {resource} não encontrado no canal"
    o = sim.payload
    items = [
        {
            "sku": (it.get("item") or {}).get("seller_sku") or "",
            "qty": int(it.get("quantity", 0)),
            "unit_price_minor": _minor(it.get("unit_price")),
        }
        for it in o.get("order_items") or []
    ]
    if not items or any(not i["sku"] or i["qty"] <= 0 for i in items):
        return "REJECTED", "itens sem SKU/quantidade"
    pay = next((p for p in o.get("payments") or [] if p.get("status") == "approved"), None)
    payment = (
        {
            "provider": "mercadopago",
            "external_id": str(pay["id"]),
            "method": pay.get("payment_method_id", "?"),
            "amount_minor": _minor(pay.get("total_paid_amount")),
        }
        if pay and o.get("status") in ML_PAID
        else None
    )
    buyer = o.get("buyer") or {}
    try:
        order, outcome = orders.ingest(
            db,
            channel="MERCADO_LIVRE",
            external_id=str(o["id"]),
            items=items,
            shipping_minor=0,
            discount_minor=0,
            customer={"name": buyer.get("nickname"), "email": None, "uf": (o.get("shipping") or {}).get("state")},
            placed_at=_parse_ts(o.get("date_created")) or dt.datetime.now(dt.UTC),
            external_updated_at=_parse_ts(o.get("last_updated")),
            payment=payment,
            cancelled=o.get("status") == "cancelled",
            source="webhook:mercadolivre",
        )
    except orders.OrderError as e:
        return "REJECTED", str(e)
    return ("IGNORED_STALE" if outcome == "STALE" else "APPLIED"), f"{outcome}: pedido {order.status}"


def process_next() -> bool:
    with session_scope() as db:
        ev = db.execute(
            select(WebhookEvent)
            .where(WebhookEvent.status == "RECEIVED")
            .order_by(WebhookEvent.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if ev is None:
            return False
        if ev.provider == "woocommerce" and ev.topic in ("product.created", "product.updated", "product.deleted"):
            status, result = _apply_woo_product(db, ev)
        elif ev.provider == "woocommerce" and ev.topic in ("order.created", "order.updated"):
            status, result = _apply_woo_order(db, ev)
        elif ev.provider == "mercadolivre" and ev.topic == "orders_v2":
            status, result = _apply_ml_order(db, ev)
        else:
            status, result = "IGNORED", f"tópico {ev.topic} sem handler"
        ev.status, ev.result, ev.processed_at = status, result, dt.datetime.now(dt.UTC)
    return True
