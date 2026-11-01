# ruff: noqa: F811  (fixtures pytest importadas: shop, ctx)
"""QA-003 (SDD §17 Segurança): replay de webhook e acesso indevido a documentos fiscais."""

import json
import uuid

from sqlalchemy import func, select

from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.models import AuditEvent, FiscalDocument, OfficeEvent, Order, Payment, WebhookEvent
from aicommerce.providers.woocommerce import sign
from tests.test_approvals import decide
from tests.test_listings import ctx, post_hook, prepare  # noqa: F401  (fixture ctx)
from tests.test_marketplaces import map_cat, note
from tests.test_orders import drain, order, shop, stock  # noqa: F401  (fixture shop)


def woo_order_body(shop, wid):
    return {
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


# ───────────── replay de webhook ─────────────
def test_replayed_signed_body_with_new_delivery_ids_has_single_effect(client, shop):
    """Atacante que capturou um webhook válido e o reenvia com delivery ids novos não duplica nada."""
    wid = int(uuid.uuid4().int % 10**8)
    body = woo_order_body(shop, wid)
    before = stock(shop["offer"]["id"])
    for _ in range(5):
        assert post_hook(client, body, topic="order.created").status_code in (200, 202)
    drain()
    with session_scope() as db:
        rows = db.execute(select(Order).where(Order.channel == "WOOCOMMERCE", Order.external_id == str(wid))).scalars()
        rows = rows.all()
        assert len(rows) == 1
        assert db.execute(select(func.count()).where(Payment.order_id == rows[0].id)).scalar_one() == 1
    assert stock(shop["offer"]["id"]) == before - 1  # reserva de estoque uma única vez


def test_tampered_or_resigned_webhook_rejected_and_not_stored(client, shop):
    wid = int(uuid.uuid4().int % 10**8)
    body = woo_order_body(shop, wid)
    raw = json.dumps(body).encode()
    forged = raw.replace(b'"total": "', b'"total": "0')  # altera depois de assinar
    delivery = uuid.uuid4().hex
    r = client.post(
        "/v1/webhooks/woocommerce",
        content=forged,
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": sign(get_settings().woo_webhook_secret, raw),  # assinatura do corpo original
            "X-WC-Webhook-Delivery-ID": delivery,
            "X-WC-Webhook-Topic": "order.created",
        },
    )
    assert r.status_code == 401
    assert post_hook(client, body, topic="order.created", secret="segredo-do-atacante").status_code == 401
    with session_scope() as db:
        assert db.execute(select(WebhookEvent).where(WebhookEvent.delivery_id == delivery)).first() is None


def test_ml_notification_replay_with_new_ids_does_not_duplicate_order(client, ctx):
    op, owner = ctx["op"], ctx["owner"]
    map_cat(client, op, "MERCADO_LIVRE", ctx["product"]["category"], "SIM-MLB1234")
    x = prepare(client, ctx, channel="MERCADO_LIVRE")
    decide(client, owner, client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op).json())
    drain()
    r = client.post("/v1/sim/mercadolivre/orders", headers=op, json={"listing_id": x["id"], "qty": 1}).json()
    drain()
    for _ in range(3):  # mesmo recurso, ids de notificação novos (o ML reenvia; atacante também pode)
        n = note(resource=r["resource"])
        assert client.post("/v1/webhooks/mercadolivre", json=n).status_code == 200
    drain()
    with session_scope() as db:
        n_orders = db.execute(
            select(func.count()).where(
                Order.channel == "MERCADO_LIVRE", Order.external_id == r["resource"].split("/")[-1]
            )
        ).scalar_one()
    assert n_orders == 1


# ───────────── ACL de documentos fiscais ─────────────
def test_fiscal_documents_require_fiscal_read_and_access_is_audited(client, shop, auth):
    o = order(client, shop)
    drain()  # emite NF-e simulada
    with session_scope() as db:
        key = db.execute(select(FiscalDocument.access_key).where(FiscalDocument.order_id == uuid.UUID(o["id"])))
        key = key.scalar_one()
    assert key and len(key) >= 8

    for role in ("viewer", "operator"):
        detail = client.get(f"/v1/orders/{o['id']}", headers=auth(role)).json()
        shown = detail["fiscal_documents"][0]["access_key"]
        assert key not in json.dumps(detail) and shown.endswith(key[-4:])
    for role in ("viewer", "operator", "admin"):
        assert client.get(f"/v1/orders/{o['id']}/fiscal-documents", headers=auth(role)).status_code == 403

    fin = auth("finance")
    full = client.get(f"/v1/orders/{o['id']}/fiscal-documents", headers=fin)
    assert full.status_code == 200 and full.json()["items"][0]["access_key"] == key
    assert full.json()["items"][0]["simulated"] is True
    with session_scope() as db:
        assert db.execute(
            select(AuditEvent).where(AuditEvent.action == "fiscal.documents.viewed", AuditEvent.subject_id == o["id"])
        ).first()


def test_office_feed_never_carries_full_access_key(client, shop):
    o = order(client, shop)
    drain()
    with session_scope() as db:
        key = db.execute(
            select(FiscalDocument.access_key).where(FiscalDocument.order_id == uuid.UUID(o["id"]))
        ).scalar_one()
        evs = db.execute(select(OfficeEvent.payload).where(OfficeEvent.type == "invoice.authorized")).scalars().all()
    assert evs and all(key not in json.dumps(p) for p in evs)


def test_channel_price_divergence_is_alerted(client, shop):
    """Achado da PoC com Woo real: pedido entrou com preço editado no canal, fora do sistema."""
    wid = int(uuid.uuid4().int % 10**8)
    body = woo_order_body(shop, wid)
    body["line_items"][0]["price"] = (shop["listing"]["price_minor"] + 427) / 100
    body["total"] = f"{(shop['listing']['price_minor'] + 427 + 1500) / 100:.2f}"
    post_hook(client, body, topic="order.created")
    drain()
    alerts = client.get("/v1/alerts", headers=shop["op"]).json()["items"]
    with session_scope() as db:
        oid = db.execute(select(Order.id).where(Order.external_id == str(wid))).scalar_one()
    assert any(a["kind"] == "ORDER_PRICE_DIVERGENCE" and a["ref"] == f"order:{oid}" for a in alerts)
    ok = order(client, shop)  # pedido no preço aprovado não alerta
    alerts = client.get("/v1/alerts", headers=shop["op"]).json()["items"]
    assert not any(a["kind"] == "ORDER_PRICE_DIVERGENCE" and a["ref"] == f"order:{ok['id']}" for a in alerts)
