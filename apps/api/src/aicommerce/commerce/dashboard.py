"""Dashboard de vendas por canal (COM-013). Agregações em SQL; margem estimada com custo do fornecedor e tarifa
esperada da tabela vigente (fictícia em DRY_RUN)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.commerce.settlements import expected_fee
from aicommerce.models import Order, OrderItem

EXCLUDED = ("CANCELLED",)
PAID = ("CAPTURED", "REFUNDED", "CHARGEBACK")  # pagamento efetivado (inclusive o que depois foi estornado)


def sales(db: Session, days: int = 30, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=days)
    orders = db.execute(select(Order).where(Order.placed_at >= since)).scalars().all()
    by: dict[str, dict[str, Any]] = {}
    daily: dict[str, int] = {}
    for o in orders:
        c = by.setdefault(
            o.channel,
            {
                "channel": o.channel,
                "orders": 0,
                "revenue_minor": 0,
                "cancelled": 0,
                "refunded": 0,
                "refund_minor": 0,
                "cost_minor": 0,
                "fees_minor": 0,
                "cost_known": True,
                "pending_payment": 0,
            },
        )
        if o.status in EXCLUDED:
            c["cancelled"] += 1
            continue
        if o.payment_status not in PAID:  # pedido não pago não é venda nem receita (auditoria A05)
            c["pending_payment"] += 1
            continue
        c["orders"] += 1
        c["revenue_minor"] += o.total_minor
        c["fees_minor"] += expected_fee(db, o.channel, o.total_minor)
        items = db.execute(select(OrderItem).where(OrderItem.order_id == o.id)).scalars().all()
        if any(i.unit_cost_minor is None for i in items):
            c["cost_known"] = False
        c["cost_minor"] += sum((i.unit_cost_minor or 0) * i.qty for i in items)
        if o.status == "REFUNDED":
            c["refunded"] += 1
            c["refund_minor"] += o.total_minor
        day = o.placed_at.astimezone(dt.UTC).date().isoformat()
        daily[day] = daily.get(day, 0) + o.total_minor
    channels = []
    for c in by.values():
        # receita líquida = bruta − reembolsos − tarifas (o que o rótulo "após tarifas e reembolsos" promete)
        net = c["revenue_minor"] - c["refund_minor"] - c["fees_minor"]
        contribution = net - c["cost_minor"]
        total = c["orders"] + c["cancelled"]
        channels.append(
            {
                **c,
                "net_revenue_minor": net,
                "contribution_minor": contribution,
                "avg_ticket_minor": (c["revenue_minor"] // c["orders"]) if c["orders"] else 0,
                "cancel_rate_bps": (c["cancelled"] * 10_000 // total) if total else 0,
                "margin_bps": (contribution * 10_000 // c["revenue_minor"]) if c["revenue_minor"] else 0,
            }
        )
    channels.sort(key=lambda r: -r["revenue_minor"])
    sim = bool(orders) and all(o.is_simulated for o in orders)
    series = [
        {
            "date": (since + dt.timedelta(days=i + 1)).date().isoformat(),
            "revenue_minor": daily.get((since + dt.timedelta(days=i + 1)).date().isoformat(), 0),
        }
        for i in range(days)
    ]
    return {
        "days": days,
        "channels": channels,
        "daily": series,
        "simulated": sim,
        "totals": {
            "orders": sum(c["orders"] for c in channels),
            "pending_payment": sum(c["pending_payment"] for c in channels),
            "net_revenue_minor": sum(c["net_revenue_minor"] for c in channels),
            "contribution_minor": sum(c["contribution_minor"] for c in channels),
        },
        "note": "Só pedidos pagos. Receita líquida = bruta − reembolsos − tarifas esperadas (tabela vigente, "
        "fictícia em DRY_RUN); contribuição = líquida − custo do fornecedor; "
        "não inclui frete pago, mídia, tributos, custos fixos nem IA — contribuição não é lucro líquido nem caixa. "
        "Margem de contribuição = contribuição ÷ receita bruta.",
    }
