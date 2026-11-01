"""Pedidos, pagamento, fiscal, compra no fornecedor, expedição e devolução (SDD §9; COM-009/010/011; FR-012/013).

Estados: NEW → PAYMENT_CONFIRMED → FISCAL_PENDING → READY_TO_FULFILL → SUPPLIER_ACCEPTED → SHIPPED → DELIVERED,
com CANCELLED, RETURN_REQUESTED, REFUNDED e INCIDENT. Cada transição é validada, registrada em order_events
(append-only), auditada e emitida ao escritório.

* Ingestão idempotente por (canal, external_id); eventos externos mais antigos que o último aplicado são ignorados.
* Anti-overbooking: reserva atômica de estoque na oferta (UPDATE … WHERE stock_qty >= qty).
* Compra no fornecedor = obrigação financeira → ActionProposal SUPPLIER_PURCHASE com aprovação humana.
* Reembolso → ActionProposal REFUND com aprovação humana. Direito de arrependimento (CDC art. 49): 7 dias.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import audit
from aicommerce.identity import pii
from aicommerce.models import (
    ApprovalProposal,
    ChannelListing,
    FiscalDocument,
    Fulfillment,
    MoneyAccount,
    Order,
    OrderEvent,
    OrderItem,
    Payment,
    Product,
    ReturnRequest,
    Supplier,
    SupplierOffer,
    Variant,
)
from aicommerce.office import events
from aicommerce.providers.fiscal import provider_for

PURCHASE_ACCOUNT = "sim-compras-brl"
WITHDRAWAL_DAYS = 7

TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"PAYMENT_CONFIRMED", "CANCELLED", "INCIDENT"},
    "PAYMENT_CONFIRMED": {"FISCAL_PENDING", "CANCELLED", "INCIDENT"},
    "FISCAL_PENDING": {"READY_TO_FULFILL", "CANCELLED", "INCIDENT"},
    "READY_TO_FULFILL": {"SUPPLIER_ACCEPTED", "CANCELLED", "INCIDENT"},
    "SUPPLIER_ACCEPTED": {"SHIPPED", "INCIDENT", "CANCELLED"},
    "SHIPPED": {"DELIVERED", "RETURN_REQUESTED", "INCIDENT"},
    "DELIVERED": {"RETURN_REQUESTED"},
    "RETURN_REQUESTED": {"REFUNDED", "DELIVERED", "INCIDENT"},
    "INCIDENT": {"READY_TO_FULFILL", "CANCELLED", "REFUNDED"},
    "CANCELLED": set(),
    "REFUNDED": set(),
}
CANCELLABLE = ("NEW", "PAYMENT_CONFIRMED", "FISCAL_PENDING", "READY_TO_FULFILL", "INCIDENT")
RETURN_REASONS = {"ARREPENDIMENTO", "DEFEITO", "NAO_RECEBIDO", "DIVERGENTE", "OUTRO"}


class OrderError(Exception):
    code = "ORDER_ERROR"
    http = 409


class NotFound(OrderError):
    code, http = "NOT_FOUND", 404


class InvalidTransition(OrderError):
    code = "INVALID_TRANSITION"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def transition(db: Session, order: Order, to: str, *, source: str, detail: dict[str, Any] | None = None) -> Order:
    if to not in TRANSITIONS[order.status]:
        raise InvalidTransition(f"{order.status} → {to} não permitido")
    before = order.status
    order.status, order.version = to, order.version + 1
    if to == "DELIVERED":
        order.delivered_at = order.delivered_at or _now()
    db.add(OrderEvent(order_id=order.id, from_status=before, to_status=to, source=source, detail=detail or {}))
    db.flush()
    audit.record(
        db,
        actor_type="system" if ":" in source or source == "system" else "user",
        actor_id=source,
        action="order.status_changed",
        subject_type="order",
        subject_id=str(order.id),
        payload={"from": before, "to": to, **(detail or {})},
    )
    events.emit(
        db,
        "order.status_changed",
        agent_id="eva" if to in ("RETURN_REQUESTED", "REFUNDED") else "logan",
        payload={
            "order_id": str(order.id),
            "external_id": order.external_id,
            "channel": order.channel,
            "from": before,
            "to": to,
            "simulated": order.is_simulated,
        },
    )
    return order


def _reserve_stock(db: Session, offer_id: uuid.UUID, qty: int) -> bool:
    row = db.execute(
        text(
            "UPDATE supplier_offers SET stock_qty = stock_qty - :q "
            "WHERE id = :id AND status = 'ACTIVE' AND stock_qty >= :q RETURNING id"
        ),
        {"q": qty, "id": offer_id},
    ).first()
    return row is not None


def _release_stock(db: Session, order: Order) -> None:
    for item in db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars():
        if item.stock_reserved and item.offer_id:
            db.execute(
                text("UPDATE supplier_offers SET stock_qty = stock_qty + :q WHERE id = :id"),
                {"q": item.qty, "id": item.offer_id},
            )
            item.stock_reserved = False


def ingest(
    db: Session,
    *,
    channel: str,
    external_id: str,
    items: list[dict[str, Any]],
    shipping_minor: int,
    discount_minor: int,
    customer: dict[str, Any],
    placed_at: dt.datetime,
    external_updated_at: dt.datetime | None,
    payment: dict[str, Any] | None,
    cancelled: bool,
    source: str,
) -> tuple[Order, str]:
    """Cria ou atualiza o pedido. Retorna (pedido, resultado) com resultado em CREATED|UPDATED|STALE|DUPLICATE."""
    existing = db.execute(
        select(Order).where(Order.channel == channel, Order.external_id == external_id).with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if external_updated_at and existing.external_updated_at and external_updated_at <= existing.external_updated_at:
            return existing, "STALE"
        existing.external_updated_at = external_updated_at or existing.external_updated_at
        changed = False
        if payment and existing.payment_status != "CAPTURED" and existing.status == "NEW":
            confirm_payment(db, existing, payment, source=source)
            changed = True
        if cancelled and existing.status in CANCELLABLE:
            cancel(db, existing, reason="cancelado no canal", source=source)
            changed = True
        return existing, "UPDATED" if changed else "DUPLICATE"

    items_total = sum(int(i["unit_price_minor"]) * int(i["qty"]) for i in items)
    order = Order(
        channel=channel,
        external_id=external_id,
        status="NEW",
        items_total_minor=items_total,
        shipping_minor=shipping_minor,
        discount_minor=discount_minor,
        total_minor=max(0, items_total + shipping_minor - discount_minor),
        uf=customer.get("uf"),
        customer_ciphertext=pii.encrypt(customer),
        customer_ref=pii.pseudonym(str(customer.get("email") or customer.get("name") or external_id)),
        placed_at=placed_at,
        external_updated_at=external_updated_at,
        is_simulated=get_settings().is_dry_run,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderEvent(order_id=order.id, from_status=None, to_status="NEW", source=source, detail={"items": len(items)})
    )
    shortage: list[str] = []
    for i in items:
        listing = None
        if i.get("listing_id"):
            listing = db.get(ChannelListing, uuid.UUID(str(i["listing_id"])))
        if listing is None:
            listing = (
                db.execute(
                    select(ChannelListing)
                    .join(Variant, Variant.id == ChannelListing.variant_id)
                    .where(Variant.sku == i["sku"], ChannelListing.channel == channel)
                )
                .scalars()
                .first()
            )
        offer = db.get(SupplierOffer, listing.offer_id) if listing else None
        reserved = bool(offer) and _reserve_stock(db, offer.id, int(i["qty"]))
        if not reserved:
            shortage.append(i["sku"])
        db.add(
            OrderItem(
                order_id=order.id,
                listing_id=listing.id if listing else None,
                variant_id=listing.variant_id if listing else None,
                offer_id=offer.id if offer else None,
                sku=i["sku"],
                qty=int(i["qty"]),
                unit_price_minor=int(i["unit_price_minor"]),
                unit_cost_minor=offer.cost_minor if offer else None,
                stock_reserved=reserved,
            )
        )
    db.flush()
    audit.record(
        db,
        actor_type="system",
        actor_id=source,
        action="order.received",
        subject_type="order",
        subject_id=str(order.id),
        payload={
            "channel": channel,
            "external_id": external_id,
            "total_minor": order.total_minor,
            "customer_ref": order.customer_ref,
        },
    )
    events.emit(
        db,
        "order.received",
        agent_id="logan",
        payload={
            "order_id": str(order.id),
            "channel": channel,
            "external_id": external_id,
            "total_minor": order.total_minor,
            "simulated": order.is_simulated,
        },
    )
    if shortage:
        order.incident_reason = f"estoque insuficiente/sem oferta para {', '.join(shortage)} (anti-overbooking)"
        transition(db, order, "INCIDENT", source=source, detail={"reason": order.incident_reason})
        return order, "CREATED"
    if payment:
        confirm_payment(db, order, payment, source=source)
    if cancelled:
        cancel(db, order, reason="cancelado no canal", source=source)
    return order, "CREATED"


def confirm_payment(db: Session, order: Order, payment: dict[str, Any], *, source: str) -> None:
    pay = db.execute(
        select(Payment).where(Payment.provider == payment["provider"], Payment.external_id == payment["external_id"])
    ).scalar_one_or_none()
    if pay is None:
        db.add(
            Payment(
                order_id=order.id,
                provider=payment["provider"],
                external_id=payment["external_id"],
                status="CAPTURED",
                amount_minor=int(payment.get("amount_minor", order.total_minor)),
                method=payment.get("method", "desconhecido"),
                captured_at=payment.get("captured_at") or _now(),
            )
        )
    order.payment_status = "CAPTURED"
    transition(db, order, "PAYMENT_CONFIRMED", source=source)
    # O marco fiscal depende do modelo aprovado; aqui a emissão é tentada logo após o pagamento (fila do worker).
    order.fiscal_status = "QUEUED"
    transition(db, order, "FISCAL_PENDING", source="system")


def process_fiscal(db: Session, order: Order) -> str:
    items = []
    for it in db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars():
        v = db.get(Variant, it.variant_id) if it.variant_id else None
        p = db.get(Product, v.product_id) if v else None
        items.append({"sku": it.sku, "qty": it.qty, "ncm": p.ncm if p else None})
    provider = provider_for(get_settings().mode.value)
    if provider is None:
        result = {
            "status": "FISCAL_HOLD",
            "reason": "sem modelo fiscal aprovado por contador nem provedor credenciado (gates TAX-01/TAX-03)",
        }
        env, prov_name = "PRODUCAO_BLOQUEADA", "nenhum"
    else:
        result = provider.issue(str(order.id), items)
        env, prov_name = provider.environment, provider.name
    doc = db.execute(
        select(FiscalDocument).where(FiscalDocument.order_id == order.id, FiscalDocument.kind == "NFE")
    ).scalar_one_or_none()
    if doc is None:
        doc = FiscalDocument(
            order_id=order.id, kind="NFE", status=result["status"], environment=env, provider=prov_name
        )
        db.add(doc)
    doc.status, doc.access_key = result["status"], result.get("access_key")
    doc.reason, doc.issued_at = result.get("reason"), result.get("issued_at")
    order.fiscal_status = result["status"]
    db.flush()
    if result["status"] == "AUTHORIZED_SIMULATED":
        events.emit(
            db,
            "invoice.authorized",
            agent_id="clara",
            # feed visível a todo office:read: só o final da chave (documento completo exige fiscal:read)
            payload={
                "order_id": str(order.id),
                "access_key_last4": (doc.access_key or "")[-4:],
                "environment": env,
                "simulated": True,
            },
        )
        transition(db, order, "READY_TO_FULFILL", source="system", detail={"fiscal": "AUTHORIZED_SIMULATED"})
    else:
        audit.record(
            db,
            actor_type="system",
            actor_id="fiscal",
            action="fiscal.hold",
            subject_type="order",
            subject_id=str(order.id),
            payload={"reason": doc.reason},
        )
    return result["status"]


def process_fiscal_next() -> bool:
    from aicommerce.db import session_scope

    with session_scope() as db:
        order = db.execute(
            select(Order)
            .where(Order.status == "FISCAL_PENDING", Order.fiscal_status == "QUEUED")
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if order is None:
            return False
        process_fiscal(db, order)
        return True


def request_fulfillment(db: Session, order: Order, *, actor: str, request_id: str | None) -> list[ApprovalProposal]:
    """Uma proposta SUPPLIER_PURCHASE por fornecedor envolvido (custo × quantidade)."""
    from aicommerce.governance.service import submit_proposal

    if order.status != "READY_TO_FULFILL":
        raise InvalidTransition(f"pedido em {order.status}; precisa estar READY_TO_FULFILL")
    existing = db.execute(
        select(Fulfillment).where(Fulfillment.order_id == order.id, Fulfillment.status.in_(("REQUESTED", "ACCEPTED")))
    ).first()
    if existing:
        raise OrderError("compra no fornecedor já solicitada")
    acc = db.execute(select(MoneyAccount).where(MoneyAccount.code == PURCHASE_ACCOUNT)).scalar_one_or_none()
    by_supplier: dict[uuid.UUID, list[OrderItem]] = {}
    for it in db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars():
        offer = db.get(SupplierOffer, it.offer_id) if it.offer_id else None
        if offer is None or it.unit_cost_minor is None:
            raise OrderError(f"item {it.sku} sem oferta de fornecedor")
        by_supplier.setdefault(offer.supplier_id, []).append(it)
    proposals = []
    for supplier_id, its in by_supplier.items():
        supplier = db.get(Supplier, supplier_id)
        cost = sum(i.unit_cost_minor * i.qty for i in its)
        payload = {
            "order_id": str(order.id),
            "order_external_id": order.external_id,
            "channel": order.channel,
            "supplier_id": str(supplier_id),
            "supplier": supplier.name,
            "ship_to_uf": order.uf,
            "items": [{"sku": i.sku, "qty": i.qty, "unit_cost_minor": i.unit_cost_minor} for i in its],
        }
        data = {
            "action_type": "SUPPLIER_PURCHASE",
            "agent_id": "logan",
            "subject_type": "order_fulfillment",
            "subject_id": f"{order.id}:{supplier_id}",
            "subject_version": order.version,
            "payload": payload,
            "risk": "MEDIUM",
            "evidence_refs": [f"order:{order.id}", f"supplier:{supplier_id}"],
            "monetary_scope_minor": cost,
            "currency": order.currency,
            "channel_account_id": None,
            "budget_account_id": acc.id if acc else None,
            "idempotency_key": f"fulfill:{order.id}:{supplier_id}:v{order.version}",
        }
        prop, _ = submit_proposal(db, data, actor=actor, request_id=request_id, golive_req=None)
        db.add(
            Fulfillment(
                order_id=order.id,
                supplier_id=supplier_id,
                status="REQUESTED",
                cost_minor=cost,
                promised_by=_now() + dt.timedelta(days=(supplier.sla_dispatch_days or 3) + 5),
                proposal_id=prop.id,
            )
        )
        proposals.append(prop)
    db.flush()
    return proposals


def cancel(db: Session, order: Order, *, reason: str, source: str) -> Order:
    if order.status not in CANCELLABLE:
        raise InvalidTransition(f"pedido em {order.status} não pode ser cancelado (use devolução)")
    _release_stock(db, order)
    order.incident_reason = reason if order.status == "INCIDENT" else order.incident_reason
    out = transition(db, order, "CANCELLED", source=source, detail={"reason": reason})
    # Já pago: o dinheiro do cliente precisa voltar. Cancelado aqui → proposta de reembolso (aprovação humana, como
    # todo reembolso). Cancelado PELO CANAL → não estorna sozinho (o canal pode já ter estornado): vira alerta.
    if order.payment_status == "CAPTURED" and reason != "cancelado no canal":
        propose_cancellation_refund(db, order, actor=source)
    return out


def propose_cancellation_refund(db: Session, order: Order, *, actor: str) -> ApprovalProposal:
    from aicommerce.governance.service import submit_proposal

    data = {
        "action_type": "REFUND",
        "agent_id": "eva",
        "subject_type": "order_cancellation",
        "subject_id": str(order.id),
        "subject_version": 1,
        "payload": {
            "order_id": str(order.id),
            "order_external_id": order.external_id,
            "channel": order.channel,
            "refund_minor": order.total_minor,
            "reason": "pedido cancelado após pagamento",
        },
        "risk": "LOW",
        "evidence_refs": [f"order:{order.id}"],
        "monetary_scope_minor": order.total_minor,
        "currency": order.currency,
        "channel_account_id": None,
        "idempotency_key": f"refund-cancel:{order.id}",
    }
    prop, _ = submit_proposal(db, data, actor=actor, request_id=None, golive_req=None)  # já emite approval.requested
    return prop


def update_shipping(
    db: Session, f: Fulfillment, *, status: str, carrier: str | None, tracking_code: str | None, source: str
) -> Fulfillment:
    order = db.get(Order, f.order_id, with_for_update=True)
    if status == "SHIPPED":
        if f.status != "ACCEPTED":
            raise InvalidTransition(f"envio em {f.status}; precisa estar ACCEPTED")
        f.status, f.carrier, f.tracking_code, f.shipped_at = "SHIPPED", carrier, tracking_code, _now()
    elif status == "DELIVERED":
        if f.status != "SHIPPED":
            raise InvalidTransition(f"envio em {f.status}; precisa estar SHIPPED")
        f.status, f.delivered_at = "DELIVERED", _now()
    else:
        raise OrderError("status de envio inválido")
    db.flush()
    siblings = (
        db.execute(select(Fulfillment).where(Fulfillment.order_id == order.id, Fulfillment.status != "FAILED"))
        .scalars()
        .all()
    )
    if (
        status == "SHIPPED"
        and all(s.status in ("SHIPPED", "DELIVERED") for s in siblings)
        and order.status == "SUPPLIER_ACCEPTED"
    ):
        transition(db, order, "SHIPPED", source=source, detail={"tracking": tracking_code, "carrier": carrier})
    if status == "DELIVERED" and all(s.status == "DELIVERED" for s in siblings) and order.status == "SHIPPED":
        transition(db, order, "DELIVERED", source=source)
    return f


def request_return(
    db: Session, order: Order, *, reason: str, description: str, actor: str, request_id: str | None
) -> tuple[ReturnRequest, ApprovalProposal]:
    from aicommerce.governance.service import submit_proposal

    if reason not in RETURN_REASONS:
        raise OrderError(f"motivo inválido; use {sorted(RETURN_REASONS)}")
    if order.status not in ("SHIPPED", "DELIVERED"):
        raise InvalidTransition(f"devolução exige pedido enviado/entregue (atual {order.status})")
    within = order.delivered_at is None or _now() - order.delivered_at <= dt.timedelta(days=WITHDRAWAL_DAYS)
    withdrawal = reason == "ARREPENDIMENTO" and within
    if reason == "ARREPENDIMENTO" and not within:
        raise OrderError("prazo de arrependimento (7 dias após o recebimento) expirado; use outro motivo")
    refund = order.total_minor  # devolução integral, inclusive frete, no arrependimento (CDC art. 49 par. único)
    ret = ReturnRequest(
        order_id=order.id,
        reason=reason,
        description=description,
        status="REQUESTED",
        withdrawal_right=withdrawal,
        refund_minor=refund,
    )
    db.add(ret)
    db.flush()
    transition(db, order, "RETURN_REQUESTED", source=actor, detail={"reason": reason, "withdrawal_right": withdrawal})
    events.emit(
        db,
        "return.requested",
        agent_id="eva",
        payload={
            "order_id": str(order.id),
            "return_id": str(ret.id),
            "reason": reason,
            "withdrawal_right": withdrawal,
            "refund_minor": refund,
        },
    )
    data = {
        "action_type": "REFUND",
        "agent_id": "eva",
        "subject_type": "order_return",
        "subject_id": str(ret.id),
        "subject_version": 1,
        "payload": {
            "order_id": str(order.id),
            "order_external_id": order.external_id,
            "channel": order.channel,
            "refund_minor": refund,
            "reason": reason,
            "withdrawal_right": withdrawal,
        },
        "risk": "LOW" if withdrawal else "MEDIUM",
        "evidence_refs": [f"order:{order.id}", f"return:{ret.id}"],
        "monetary_scope_minor": refund,
        "currency": order.currency,
        "channel_account_id": None,
        "idempotency_key": f"refund:{ret.id}",
    }
    prop, _ = submit_proposal(db, data, actor=actor, request_id=request_id, golive_req=None)
    ret.proposal_id = prop.id
    return ret, prop


def on_execution_result(db: Session, prop: ApprovalProposal, *, succeeded: bool, provider_ref: str | None) -> None:
    """Hook do executor para SUPPLIER_PURCHASE e REFUND."""
    from aicommerce.finance import ledger

    if prop.subject_type == "order_fulfillment":
        f = db.execute(
            select(Fulfillment).where(Fulfillment.proposal_id == prop.id).with_for_update()
        ).scalar_one_or_none()
        if f is None:
            return
        order = db.get(Order, f.order_id, with_for_update=True)
        if succeeded:
            f.status, f.supplier_order_ref = "ACCEPTED", provider_ref
            if prop.budget_account_id:  # o fornecedor foi pago: gasto reconciliado consome a reserva
                from aicommerce.models import AuthorizationGrant

                grant = db.execute(
                    select(AuthorizationGrant).where(AuthorizationGrant.proposal_id == prop.id)
                ).scalar_one_or_none()
                ledger.record_spend(
                    db,
                    prop.budget_account_id,
                    amount_minor=f.cost_minor,
                    currency=prop.currency,
                    external_ref=provider_ref or str(prop.id),
                    idempotency_key=f"purchase:{prop.id}",
                    actor="executor",
                    reservation_id=grant.reservation_id if grant else None,
                )
            pending = db.execute(
                select(Fulfillment).where(Fulfillment.order_id == order.id, Fulfillment.status == "REQUESTED")
            ).first()
            if not pending and order.status == "READY_TO_FULFILL":
                transition(db, order, "SUPPLIER_ACCEPTED", source="executor", detail={"supplier_ref": provider_ref})
        else:
            f.status = "FAILED"
            if order.status == "READY_TO_FULFILL":
                order.incident_reason = "compra no fornecedor falhou"
                transition(db, order, "INCIDENT", source="executor", detail={"reason": order.incident_reason})
    elif prop.subject_type == "order_cancellation":
        order = db.get(Order, uuid.UUID(prop.subject_id), with_for_update=True)
        if order is not None and succeeded:  # pedido continua CANCELADO; só o pagamento passa a estornado
            for pay in db.execute(select(Payment).where(Payment.order_id == order.id)).scalars():
                pay.status = "REFUNDED"
            order.payment_status = "REFUNDED"
            events.emit(
                db,
                "order.status_changed",
                agent_id="eva",
                payload={"order_id": str(order.id), "status": order.status, "payment_status": "REFUNDED"},
            )
    elif prop.subject_type == "order_return":
        ret = db.get(ReturnRequest, uuid.UUID(prop.subject_id), with_for_update=True)
        if ret is None:
            return
        order = db.get(Order, ret.order_id, with_for_update=True)
        if succeeded:
            ret.status = "REFUNDED"
            for pay in db.execute(select(Payment).where(Payment.order_id == order.id)).scalars():
                pay.status = "REFUNDED"
            order.payment_status = "REFUNDED"
            transition(db, order, "REFUNDED", source="executor", detail={"refund_ref": provider_ref})
        else:
            ret.status = "REFUND_FAILED"
            order.incident_reason = "reembolso falhou"
            transition(db, order, "INCIDENT", source="executor", detail={"reason": "reembolso falhou"})


def on_closed_without_execution(db: Session, prop: ApprovalProposal) -> None:
    """Proposta encerrada sem execução (rejeitada/expirada/revogada/pedido de mudança).

    Compra no fornecedor: o envio solicitado é cancelado e pode ser pedido de novo.
    Reembolso rejeitado: devolução negada; o pedido volta ao estado anterior (DELIVERED)."""
    if prop.subject_type == "order_fulfillment":
        f = db.execute(
            select(Fulfillment).where(Fulfillment.proposal_id == prop.id).with_for_update()
        ).scalar_one_or_none()
        if f and f.status == "REQUESTED":
            f.status = "CANCELLED"
        return
    if prop.subject_type == "order_return" and prop.status in ("REJECTED",):
        ret = db.get(ReturnRequest, uuid.UUID(prop.subject_id), with_for_update=True)
        if ret and ret.status == "REQUESTED":
            ret.status = "REJECTED"
            order = db.get(Order, ret.order_id, with_for_update=True)
            if order.status == "RETURN_REQUESTED":
                transition(db, order, "DELIVERED", source="approval", detail={"return": "rejeitada"})


def customer_view(db: Session, order: Order, *, can_read_pii: bool, actor: str) -> dict[str, Any]:
    data = pii.decrypt(order.customer_ciphertext) if order.customer_ciphertext else {}
    if not can_read_pii:
        return pii.mask(data)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="order.pii.viewed",
        subject_type="order",
        subject_id=str(order.id),
        payload={"fields": sorted(data)},
    )
    return data
