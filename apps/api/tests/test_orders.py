import datetime as dt
import json
import threading
import uuid

import pytest
from sqlalchemy import select, text

from aicommerce.commerce import orders, settlements, webhooks
from aicommerce.db import session_scope
from aicommerce.finance import ledger
from aicommerce.governance import executor
from aicommerce.models import AuditEvent, ChannelListing, MoneyAccount, Order, SupplierOffer
from tests.test_approvals import decide
from tests.test_listings import post_hook, prepare
from tests.test_suppliers import approve_supplier, make_ready, mk_product, mk_supplier


def drain():
    while (
        executor.execute_next() or executor.reconcile_next() or orders.process_fiscal_next() or webhooks.process_next()
    ):
        pass


@pytest.fixture
def shop(client, auth):
    """Anúncio WooCommerce publicado (fluxo real: prepare → propose → approve → executor)."""
    op, owner = auth("operator"), auth("owner")
    prod = mk_product(client, op)
    sup = mk_supplier(client, op)
    vid = prod["variants"][0]["id"]
    offer = make_ready(client, op, owner, sup, vid)
    approve_supplier(client, owner, sup["id"])
    c = {"op": op, "owner": owner, "variant_id": vid, "offer": offer}
    x = prepare(client, c)
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op).json()
    d = decide(client, owner, prop)
    assert d.status_code == 200, f"aprovação falhou: {d.status_code} {d.text[:300]}"
    drain()
    r = client.get(f"/v1/listings/{x['id']}", headers=op)
    assert r.status_code == 200, f"anúncio indisponível: {r.status_code} {r.text[:300]}"
    assert r.json()["status"] == "PUBLISHED", f"anúncio não publicado: {r.json().get('status')}"
    return {**c, "listing": r.json(), "sku": prod["variants"][0]["sku"]}


def order(client, shop, qty=1, paid=True):
    r = client.post(
        "/v1/sim/orders", headers=shop["op"], json={"listing_id": shop["listing"]["id"], "qty": qty, "paid": paid}
    )
    assert r.status_code == 201, r.text
    return r.json()


def get(client, shop, oid):
    return client.get(f"/v1/orders/{oid}", headers=shop["op"]).json()


def stock(offer_id):
    with session_scope() as db:
        return db.get(SupplierOffer, uuid.UUID(offer_id)).stock_qty


def purchase_spend():
    with session_scope() as db:
        acc = db.execute(select(MoneyAccount).where(MoneyAccount.code == "sim-compras-brl")).scalar_one()
        return ledger.budget_view(db, acc.id).reconciled_spend_minor


def to_delivered(client, shop, o):
    drain()  # fiscal simulado
    props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=shop["op"]).json()["proposals"]
    for p in props:
        assert decide(client, shop["owner"], p).status_code == 200
    drain()
    f = get(client, shop, o["id"])["fulfillments"][0]
    client.post(f"/v1/sim/fulfillments/{f['id']}/ship", headers=shop["op"])
    client.post(f"/v1/sim/fulfillments/{f['id']}/deliver", headers=shop["op"])
    return get(client, shop, o["id"])


def test_happy_path_order_to_delivery(client, shop):
    before_stock, before_spend = stock(shop["offer"]["id"]), purchase_spend()
    o = order(client, shop, qty=2)
    assert o["status"] == "FISCAL_PENDING" and o["payment_status"] == "CAPTURED" and o["simulated"]
    assert stock(shop["offer"]["id"]) == before_stock - 2
    drain()
    o2 = get(client, shop, o["id"])
    assert o2["status"] == "READY_TO_FULFILL"
    doc = o2["fiscal_documents"][0]
    assert doc["status"] == "AUTHORIZED_SIMULATED" and doc["environment"] == "SIMULACAO_SEM_VALOR_FISCAL"
    assert "sem valor fiscal" in doc["reason"]
    props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=shop["op"]).json()["proposals"]
    assert len(props) == 1 and props[0]["action_type"] == "SUPPLIER_PURCHASE"
    assert (
        props[0]["status"] == "PENDING_APPROVAL" and props[0]["monetary_scope_minor"] == 2 * shop["offer"]["cost_minor"]
    )
    assert client.post(f"/v1/orders/{o['id']}/fulfill", headers=shop["op"]).status_code == 409  # sem duplicar
    decide(client, shop["owner"], props[0])
    drain()
    o3 = get(client, shop, o["id"])
    assert o3["status"] == "SUPPLIER_ACCEPTED" and o3["fulfillments"][0]["status"] == "ACCEPTED"
    assert purchase_spend() == before_spend + 2 * shop["offer"]["cost_minor"]
    f = o3["fulfillments"][0]
    assert client.post(f"/v1/sim/fulfillments/{f['id']}/deliver", headers=shop["op"]).status_code == 409
    client.post(f"/v1/sim/fulfillments/{f['id']}/ship", headers=shop["op"])
    client.post(f"/v1/sim/fulfillments/{f['id']}/deliver", headers=shop["op"])
    final = get(client, shop, o["id"])
    assert final["status"] == "DELIVERED" and final["delivered_at"]
    assert [e["to"] for e in final["events"]] == [
        "NEW",
        "PAYMENT_CONFIRMED",
        "FISCAL_PENDING",
        "READY_TO_FULFILL",
        "SUPPLIER_ACCEPTED",
        "SHIPPED",
        "DELIVERED",
    ]


def test_withdrawal_refund_flow(client, shop):
    o = to_delivered(client, shop, order(client, shop))
    r = client.post(f"/v1/orders/{o['id']}/returns", headers=shop["op"], json={"reason": "ARREPENDIMENTO"})
    body = r.json()
    assert r.status_code == 201 and body["withdrawal_right"] is True and body["refund_minor"] == o["total_minor"]
    assert body["proposal"]["action_type"] == "REFUND" and body["proposal"]["status"] == "PENDING_APPROVAL"
    decide(client, shop["owner"], body["proposal"])
    drain()
    final = get(client, shop, o["id"])
    assert final["status"] == "REFUNDED" and final["payment_status"] == "REFUNDED"
    assert final["returns"][0]["status"] == "REFUNDED"


def test_rejected_refund_returns_to_delivered_and_late_withdrawal_refused(client, shop):
    o = to_delivered(client, shop, order(client, shop))
    body = client.post(
        f"/v1/orders/{o['id']}/returns", headers=shop["op"], json={"reason": "DEFEITO", "description": "riscado"}
    ).json()
    assert body["withdrawal_right"] is False
    decide(client, shop["owner"], body["proposal"], action="reject", comment="laudo não confirma defeito")
    assert get(client, shop, o["id"])["status"] == "DELIVERED"
    with session_scope() as db:
        db.get(Order, uuid.UUID(o["id"])).delivered_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=8)
    r = client.post(f"/v1/orders/{o['id']}/returns", headers=shop["op"], json={"reason": "ARREPENDIMENTO"})
    assert r.status_code == 409 and "7 dias" in r.json()["detail"]["message"]


def test_anti_overbooking(client, shop):
    with session_scope() as db:
        db.get(SupplierOffer, uuid.UUID(shop["offer"]["id"])).stock_qty = 1
    o = order(client, shop, qty=2)
    assert o["status"] == "INCIDENT" and "anti-overbooking" in o["incident_reason"]
    assert stock(shop["offer"]["id"]) == 1
    results = []
    barrier = threading.Barrier(5)

    def buy():
        barrier.wait()
        with session_scope() as db:
            results.append(orders._reserve_stock(db, uuid.UUID(shop["offer"]["id"]), 1))

    ts = [threading.Thread(target=buy) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results.count(True) == 1 and stock(shop["offer"]["id"]) == 0


def test_cancel_releases_stock(client, shop):
    before = stock(shop["offer"]["id"])
    o = order(client, shop, qty=3)
    assert stock(shop["offer"]["id"]) == before - 3
    r = client.post(f"/v1/orders/{o['id']}/cancel", headers=shop["op"], json={"reason": "cliente desistiu"})
    assert r.json()["status"] == "CANCELLED" and stock(shop["offer"]["id"]) == before


def test_missing_ncm_holds_fiscal_and_alerts(client, shop):
    with session_scope() as db:
        db.execute(
            text("UPDATE products SET ncm = NULL WHERE id = (SELECT product_id FROM variants WHERE id = :v)"),
            {"v": shop["variant_id"]},
        )
    o = order(client, shop)
    drain()
    o2 = get(client, shop, o["id"])
    assert o2["status"] == "FISCAL_PENDING" and o2["fiscal_status"] == "FISCAL_HOLD"
    alerts = client.get("/v1/alerts", headers=shop["op"]).json()["items"]
    assert any(a["kind"] == "FISCAL_HOLD" and a["ref"] == f"order:{o['id']}" for a in alerts)


def test_live_mode_always_holds_fiscal(client, shop, monkeypatch):
    from aicommerce.config import RuntimeMode

    o = order(client, shop)

    class Live:
        mode = RuntimeMode.LIVE
        is_dry_run = False

    monkeypatch.setattr(orders, "get_settings", lambda: Live())
    with session_scope() as db:
        assert orders.process_fiscal(db, db.get(Order, uuid.UUID(o["id"]))) == "FISCAL_HOLD"
    assert "TAX-01" in get(client, shop, o["id"])["fiscal_documents"][0]["reason"]


def test_pii_encrypted_masked_and_access_audited(client, shop, auth):
    o = order(client, shop)
    with session_scope() as db:
        row = db.get(Order, uuid.UUID(o["id"]))
        assert "cliente.simulado" not in row.customer_ciphertext and len(row.customer_ref) == 32
    masked = client.get(f"/v1/orders/{o['id']}", headers=auth("viewer")).json()["customer"]
    assert masked["name"] == "C***" and "email" not in masked
    assert client.get(f"/v1/orders/{o['id']}/customer", headers=auth("viewer")).status_code == 403
    full = client.get(f"/v1/orders/{o['id']}/customer", headers=shop["op"]).json()
    assert full["email"].endswith("@cliente.simulado.invalid")
    with session_scope() as db:
        assert db.execute(
            select(AuditEvent).where(AuditEvent.action == "order.pii.viewed", AuditEvent.subject_id == o["id"])
        ).first()


def test_woo_order_webhook_idempotent_and_ordered(client, shop):
    wid = int(uuid.uuid4().int % 10**8)
    body = {
        "id": wid,
        "status": "processing",
        "date_created_gmt": "2026-10-24T10:00:00",
        "date_modified_gmt": "2026-10-24T10:00:00",
        "date_paid_gmt": "2026-10-24T10:00:05",
        "total": f"{(shop['listing']['price_minor'] + 1500) / 100:.2f}",
        "shipping_total": "15.00",
        "discount_total": "0.00",
        "payment_method": "pix",
        "transaction_id": f"tx-{wid}",
        "billing": {"first_name": "Ana", "last_name": "Teste", "email": "ana@exemplo.invalid", "state": "SP"},
        "line_items": [{"sku": shop["sku"], "quantity": 1, "price": shop["listing"]["price_minor"] / 100}],
    }
    d1 = uuid.uuid4().hex
    post_hook(client, body, delivery=d1, topic="order.created")
    post_hook(client, body, delivery=d1, topic="order.created")  # reentrega
    drain()
    with session_scope() as db:
        rows = (
            db.execute(select(Order).where(Order.channel == "WOOCOMMERCE", Order.external_id == str(wid)))
            .scalars()
            .all()
        )
        assert len(rows) == 1 and rows[0].payment_status == "CAPTURED"
        oid = rows[0].id
    old = {**body, "status": "cancelled", "date_modified_gmt": "2026-10-24T09:00:00"}
    post_hook(client, old, topic="order.updated")
    drain()
    assert get(client, shop, str(oid))["status"] == "READY_TO_FULFILL"  # evento antigo ignorado
    new = {**body, "status": "cancelled", "date_modified_gmt": "2026-10-24T11:00:00"}
    post_hook(client, new, topic="order.updated")
    drain()
    assert get(client, shop, str(oid))["status"] == "CANCELLED"


def test_settlement_reconciliation_and_divergence(client, shop, auth):
    fin = auth("finance")
    o = order(client, shop)
    r = client.post("/v1/sim/settlements", headers=shop["op"], json={"channel": "WOOCOMMERCE"})
    assert r.status_code == 201
    rec = client.get("/v1/finance/reconciliation?channel=WOOCOMMERCE", headers=fin).json()
    assert any(m["order_external_id"] == o["external_id"] for m in rec["matched"])
    o2 = order(client, shop)
    client.post("/v1/sim/settlements", headers=shop["op"], json={"channel": "WOOCOMMERCE", "fee_error_minor": 37})
    rec = client.get("/v1/finance/reconciliation?channel=WOOCOMMERCE", headers=fin).json()
    div = [d for d in rec["divergent"] if d["order_external_id"] == o2["external_id"]]
    assert div and div[0]["diff_minor"] == -37
    assert any(a["kind"] == "SETTLEMENT_DIVERGENCE" for a in client.get("/v1/alerts", headers=fin).json()["items"])
    imp = {
        "channel": "WOOCOMMERCE",
        "external_id": f"EXT-{uuid.uuid4().hex[:6]}",
        "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-10-07T00:00:00Z",
        "lines": [{"order_external_id": "NAO-EXISTE", "gross_minor": 1000, "fee_minor": 100}],
    }
    assert client.post("/v1/finance/settlements/import", headers=fin, json=imp).json()["replayed"] is False
    assert client.post("/v1/finance/settlements/import", headers=fin, json=imp).json()["replayed"] is True
    rec = client.get("/v1/finance/reconciliation?channel=WOOCOMMERCE", headers=fin).json()
    assert any(x["order_external_id"] == "NAO-EXISTE" for x in rec["orphans"])
    assert client.post("/v1/finance/settlements/import", headers=auth("viewer"), json=imp).status_code == 403


def test_emergency_stop_keeps_serving_contracted_orders(client, shop):
    o = order(client, shop)
    drain()
    client.post("/v1/emergency-stop", json={"reason": "teste pedidos"}, headers=shop["owner"])
    try:
        props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=shop["op"]).json()["proposals"]
        assert decide(client, shop["owner"], props[0]).status_code == 200
        drain()
        assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED"
    finally:
        client.post("/v1/emergency-stop/release", json={"reason": "fim"}, headers=shop["owner"])


def test_sim_routes_unavailable_outside_dry_run(client, shop, monkeypatch):
    from aicommerce.api import routes_orders

    class Live:
        is_dry_run = False

    monkeypatch.setattr(routes_orders, "get_settings", lambda: Live())
    r = client.post("/v1/sim/orders", headers=shop["op"], json={"listing_id": shop["listing"]["id"]})
    assert r.status_code == 404


def test_unpublished_listing_cannot_receive_sim_order(client, shop):
    with session_scope() as db:
        db.get(ChannelListing, uuid.UUID(shop["listing"]["id"])).status = "PAUSED"
    r = client.post("/v1/sim/orders", headers=shop["op"], json={"listing_id": shop["listing"]["id"]})
    assert r.status_code == 409


_ = (json, settlements)


def test_dashboard_counts_only_paid_orders_and_net_is_after_fees(client, shop, auth):
    """Auditoria A05: pedido não pago não é venda; receita líquida desconta reembolsos E tarifas."""
    from aicommerce.commerce import dashboard
    from aicommerce.db import session_scope

    with session_scope() as db:
        before = {c["channel"]: c for c in dashboard.sales(db, 1)["channels"]}
    paid = order(client, shop, paid=True)
    order(client, shop, paid=False)
    with session_scope() as db:
        now = {c["channel"]: c for c in dashboard.sales(db, 1)["channels"]}
    ch = paid["channel"]
    b = before.get(ch, {"orders": 0, "revenue_minor": 0, "pending_payment": 0, "fees_minor": 0})
    c = now[ch]
    assert c["orders"] - b["orders"] == 1 and c["revenue_minor"] - b["revenue_minor"] == paid["total_minor"]
    assert c["pending_payment"] - b.get("pending_payment", 0) == 1
    assert c["fees_minor"] > b["fees_minor"]
    assert c["net_revenue_minor"] == c["revenue_minor"] - c["refund_minor"] - c["fees_minor"]
