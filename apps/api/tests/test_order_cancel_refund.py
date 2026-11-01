"""Pedido PAGO e depois cancelado: o dinheiro do cliente volta (revisão funcional da finalização).
Cancelado aqui → proposta de reembolso com aprovação humana; ao executar, pagamento estornado e pedido segue
cancelado. Cancelado pelo canal → sem estorno automático (evita estorno em dobro), mas alerta crítico até conferir."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from aicommerce.commerce import alerts, orders
from aicommerce.db import session_scope
from aicommerce.models import ApprovalProposal, Order
from tests.test_approvals import decide
from tests.test_orders import drain, order, shop  # noqa: F401 — fixture


def pending_refund_alert(order_id: str) -> bool:
    with session_scope() as db:
        return any(a["kind"] == "REFUND_PENDING" and a["ref"] == f"order:{order_id}" for a in alerts.compute(db))


def test_cancelling_a_paid_order_proposes_refund_and_executes_after_approval(client, shop):  # noqa: F811
    o = order(client, shop, paid=True)
    r = client.post(f"/v1/orders/{o['id']}/cancel", headers=shop["op"], json={"reason": "cliente desistiu"})
    assert r.json()["status"] == "CANCELLED"
    with session_scope() as db:
        prop = db.execute(
            select(ApprovalProposal).where(
                ApprovalProposal.subject_type == "order_cancellation", ApprovalProposal.subject_id == o["id"]
            )
        ).scalar_one()
        assert prop.action_type == "REFUND" and prop.status == "PENDING_APPROVAL"
        assert prop.payload["refund_minor"] == db.get(Order, uuid.UUID(o["id"])).total_minor
        prop_out = {"id": str(prop.id), "payload_sha256": prop.payload_sha256, "subject_version": 1}
    assert pending_refund_alert(o["id"])  # até o estorno acontecer, o alerta crítico fica visível
    assert decide(client, shop["owner"], prop_out).status_code == 200
    drain()
    with session_scope() as db:
        got = db.get(Order, uuid.UUID(o["id"]))
        assert got.status == "CANCELLED" and got.payment_status == "REFUNDED"
    assert not pending_refund_alert(o["id"])


def test_unpaid_cancellation_needs_no_refund(client, shop):  # noqa: F811
    o = order(client, shop, paid=False)
    client.post(f"/v1/orders/{o['id']}/cancel", headers=shop["op"], json={"reason": "não pagou"})
    with session_scope() as db:
        n = db.execute(select(ApprovalProposal).where(ApprovalProposal.subject_id == o["id"])).all()
    assert n == [] and not pending_refund_alert(o["id"])


def test_channel_cancellation_of_paid_order_alerts_instead_of_refunding_twice(client, shop):  # noqa: F811
    o = order(client, shop, paid=True)
    with session_scope() as db:
        got = db.get(Order, uuid.UUID(o["id"]), with_for_update=True)
        orders.cancel(db, got, reason="cancelado no canal", source="webhook:woocommerce")
    with session_scope() as db:
        n = db.execute(select(ApprovalProposal).where(ApprovalProposal.subject_id == o["id"])).all()
    assert n == [] and pending_refund_alert(o["id"])
