"""Alertas operacionais (FR-021): estoque obsoleto, atrasos, fiscal retido, incidentes, execuções incertas,
margem negativa e divergências de conciliação. Calculados sob demanda a partir do estado autoritativo."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.commerce import settlements, suppliers
from aicommerce.models import ChannelListing, ExecutionJob, Fulfillment, Order, OrderItem, SupplierOffer

WORKER_STALE_S = 120


def compute(db: Session, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    now = now or dt.datetime.now(dt.UTC)
    out: list[dict[str, Any]] = []

    def add(severity: str, kind: str, message: str, ref: str) -> None:
        out.append({"severity": severity, "kind": kind, "message": message, "ref": ref})

    for o in db.execute(select(SupplierOffer).where(SupplierOffer.status == "ACTIVE")).scalars():
        if now - o.stock_observed_at > suppliers.STOCK_FRESHNESS:
            add("WARNING", "STOCK_STALE", f"estoque da oferta {str(o.id)[:8]} sem observação há >24 h", f"offer:{o.id}")
    for f in db.execute(
        select(Fulfillment).where(Fulfillment.status.in_(("REQUESTED", "ACCEPTED")), Fulfillment.promised_by < now)
    ).scalars():
        add(
            "CRITICAL", "SHIPMENT_LATE", f"envio atrasado (prometido para {f.promised_by:%d/%m})", f"order:{f.order_id}"
        )
    for o in db.execute(select(Order).where(Order.fiscal_status == "FISCAL_HOLD")).scalars():
        add("CRITICAL", "FISCAL_HOLD", f"pedido {o.external_id} com emissão fiscal retida", f"order:{o.id}")
    for o in db.execute(
        select(Order).where(Order.status == "CANCELLED", Order.payment_status == "CAPTURED")
    ).scalars():  # cliente pagou e o pedido foi cancelado: o estorno precisa acontecer (e ser conferido)
        add(
            "CRITICAL",
            "REFUND_PENDING",
            f"pedido {o.external_id} cancelado com pagamento capturado: reembolso pendente ou a conferir no canal",
            f"order:{o.id}",
        )
    from aicommerce.models import ApprovalProposal, RobotRun

    stuck = (
        select(RobotRun)
        .join(ApprovalProposal, ApprovalProposal.id == RobotRun.proposal_id)
        .where(RobotRun.state.in_(("NEEDS_OWNER", "FAILED")), ApprovalProposal.status == "AUTHORIZED")
    )  # proposta revogada/rejeitada/expirada: o assunto está encerrado, sem alerta
    for r in db.execute(stuck).scalars():
        sev = "CRITICAL" if r.state == "NEEDS_OWNER" else "WARNING"
        add(
            sev, f"ROBOT_{r.state}", f"robô de publicação parou em '{r.step}': {r.message}", f"proposal:{r.proposal_id}"
        )
    for o in db.execute(select(Order).where(Order.status == "INCIDENT")).scalars():
        add(
            "CRITICAL", "ORDER_INCIDENT", f"pedido {o.external_id}: {o.incident_reason or 'incidente'}", f"order:{o.id}"
        )
    for j in db.execute(select(ExecutionJob).where(ExecutionJob.status == "UNKNOWN")).scalars():
        add(
            "CRITICAL",
            "EXECUTION_UNKNOWN",
            f"execução {j.action_type} com resultado incerto (reconciliando)",
            f"job:{j.id}",
        )
    for o in db.execute(select(Order).where(Order.status.not_in(("CANCELLED", "REFUNDED")))).scalars():
        items = db.execute(select(OrderItem).where(OrderItem.order_id == o.id)).scalars().all()
        if items and all(i.unit_cost_minor is not None for i in items):
            cost = sum(i.unit_cost_minor * i.qty for i in items)
            fees = settlements.expected_fee(db, o.channel, o.total_minor)
            if o.total_minor - fees - cost < 0:
                add("WARNING", "NEGATIVE_MARGIN_ORDER", f"pedido {o.external_id} com margem negativa", f"order:{o.id}")
    # Preço cobrado pelo canal ≠ qualquer preço aprovado do anúncio (edição fora do sistema, promoção do canal).
    from aicommerce.models import ChannelListing as _CL
    from aicommerce.models import ListingVersion

    approved: dict[Any, set[int]] = {}
    open_orders = select(Order.id).where(Order.status.not_in(("CANCELLED", "REFUNDED", "DELIVERED")))
    for it in db.execute(
        select(OrderItem).where(OrderItem.order_id.in_(open_orders), OrderItem.listing_id.is_not(None))
    ).scalars():
        if it.listing_id not in approved:
            lst = db.get(_CL, it.listing_id)
            prices = {lst.price_minor} if lst else set()
            for v in db.execute(select(ListingVersion.pricing).where(ListingVersion.listing_id == it.listing_id)):
                p = ((v.pricing or {}).get("quote") or {}).get("breakdown") or {}
                if p.get("price_minor"):
                    prices.add(int(p["price_minor"]))
            approved[it.listing_id] = prices
        if approved[it.listing_id] and it.unit_price_minor not in approved[it.listing_id]:
            add(
                "WARNING",
                "ORDER_PRICE_DIVERGENCE",
                f"item {it.sku}: canal cobrou {it.unit_price_minor} centavos, preço aprovado "
                f"{sorted(approved[it.listing_id])} — conferir edição fora do sistema ou promoção",
                f"order:{it.order_id}",
            )
    from aicommerce.models import SystemFlag
    from aicommerce.observability import HEARTBEAT_KEY

    hb = db.get(SystemFlag, HEARTBEAT_KEY)
    if hb is not None and (now - hb.updated_at).total_seconds() > WORKER_STALE_S:
        add(
            "CRITICAL",
            "WORKER_DOWN",
            f"worker sem ciclo há {int((now - hb.updated_at).total_seconds())} s: "
            "filas de execução, fiscal e webhooks paradas",
            "worker",
        )
    rec = settlements.reconcile(db, now=now)
    for d in rec["divergent"]:
        add(
            "WARNING",
            "SETTLEMENT_DIVERGENCE",
            f"repasse do pedido {d['order_external_id']} difere em {d['diff_minor']} centavos",
            f"order:{d['order_id']}",
        )
    for d in rec["disputes"]:
        add(
            "CRITICAL",
            "CHARGEBACK_OPEN",
            f"chargeback no pedido {d['order_external_id']}: {d['at_risk_minor']} centavos debitados em disputa",
            f"order:{d['order_id']}",
        )
    for r in rec["open_receivables"]:
        if r["overdue"]:
            add(
                "WARNING",
                "RECEIVABLE_OVERDUE",
                f"pedido {r['order_external_id']} sem repasse há {r['days']} dias",
                f"order:{r['order_id']}",
            )
    from aicommerce.commerce import ads
    from aicommerce.governance import killswitch

    for sev, kind, message, ref in ads.alerts(db, emergency_stop=killswitch.is_active(db)):
        add(sev, kind, message, ref)
    if killswitch.is_active(db):
        for r in db.execute(select(ChannelListing).where(ChannelListing.status == "PUBLISHED")).scalars():
            add("CRITICAL", "LISTING_ACTIVE_DURING_STOP", f"anúncio {r.channel} ainda publicado", f"listing:{r.id}")
    order = {"CRITICAL": 0, "WARNING": 1}
    return sorted(out, key=lambda a: order[a["severity"]])
