import datetime as dt
import hashlib
import hmac
import uuid

import pytest
from sqlalchemy import select

from aicommerce.commerce import channels, webhooks
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.governance import executor
from aicommerce.models import ChannelAccount, Order, WebhookEvent
from aicommerce.providers import marketplaces as mp
from tests.test_approvals import decide
from tests.test_listings import ctx, prepare  # noqa: F401  (fixture ctx)


def drain():
    while executor.execute_next() or executor.reconcile_next() or webhooks.process_next():
        pass


def map_cat(client, h, channel, category, ext):
    r = client.put(
        f"/v1/channels/{channel}/categories",
        headers=h,
        json={"internal_category": category, "external_category_id": ext},
    )
    assert r.status_code == 200


# ───────────── Contratos ─────────────
def test_ml_item_mapping_validates_and_rejects_bad():
    item = mp.to_ml_item(
        title="x" * 80,
        category_id="MLB1234",
        price_minor=8990,
        stock_qty=3,
        images=["https://i/1.jpg"],
        sku="S1",
        brand="Marca",
        gtin="7890000000017",
    )
    assert mp.validate_payload("MERCADO_LIVRE", item) == []
    assert len(item["title"]) == 60 and item["price"] == 89.9
    assert {"id": "GTIN", "value_name": "7890000000017"} in item["attributes"]
    assert mp.validate_payload("MERCADO_LIVRE", {**item, "currency_id": "USD"})
    assert mp.validate_payload("MERCADO_LIVRE", {**item, "category_id": "casa"})


def test_shopee_and_tts_mapping_and_signatures():
    s = mp.to_shopee_item(
        title="Garrafa",
        description="",
        price_minor=6563,
        stock_qty=10,
        weight_g=380,
        dims_mm=(75, 75, 260),
        category_id=100123,
        sku="S1",
    )
    assert mp.validate_payload("SHOPEE", s) == [] and s["weight"] == 0.38 and s["dimension"]["package_height"] == 26
    t = mp.to_tts_product(
        title="Garrafa", description="d", category_id="600001", price_minor=6563, stock_qty=4, sku="S1", weight_g=380
    )
    assert mp.validate_payload("TIKTOK_SHOP", t) == [] and t["skus"][0]["price"] == {
        "amount": "65.63",
        "currency": "BRL",
    }
    sig = mp.shopee_sign(1001, "/api/v2/product/add_item", 1700000000, "chave", "tok", 55)
    expected = hmac.new(b"chave", b"1001/api/v2/product/add_item1700000000tok55", hashlib.sha256).hexdigest()
    assert sig == expected
    a = mp.tts_sign(
        "seg", "/product/202309/products", {"app_key": "k", "timestamp": "1", "sign": "x", "access_token": "y"}, "{}"
    )
    assert a == hmac.new(b"seg", b"seg/product/202309/productsapp_keyktimestamp1{}seg", hashlib.sha256).hexdigest()


def test_tiktok_blocks_international_and_contract_warnings(client, ctx):  # noqa: F811
    from tests.test_suppliers import mk_supplier

    op = ctx["op"]
    map_cat(client, op, "TIKTOK_SHOP", ctx["product"]["category"], "600001")
    x = prepare(client, ctx, channel="TIKTOK_SHOP")
    codes = {i["code"] for i in x["compliance"]}
    assert "CONTRACT_UNVERIFIED" in codes and x["blockers"] == []
    intl = mk_supplier(client, op, origin="INTERNATIONAL")
    offer = client.post(
        f"/v1/suppliers/{intl['id']}/offers",
        headers=op,
        json={
            "variant_id": ctx["variant_id"],
            "cost_minor": 1500,
            "lead_time_days": 20,
            "stock_qty": 10,
            "source_ref": "cotacao:x",
        },
    ).json()
    x2 = prepare(client, {**ctx, "offer": offer}, channel="TIKTOK_SHOP", fulfillment_mode="CROSS_BORDER")
    assert "TTS_DOMESTIC_ONLY" in {b["code"] for b in x2["blockers"]}


# ───────────── OAuth e tokens ─────────────
def test_pkce_and_authorize_url():
    verifier, challenge = mp.pkce_pair()
    import base64

    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = mp.ml_authorize_url("123", "https://app.exemplo/cb", "st", challenge)
    assert url.startswith("https://auth.mercadolivre.com.br/authorization?response_type=code&client_id=123")
    assert "code_challenge_method=S256" in url
    with pytest.raises(ValueError):
        mp.ml_token_request("password")


def test_refresh_token_is_single_use():
    oauth = mp.MockMLOAuth()
    t = oauth.issue()
    t2 = oauth.refresh(t["refresh_token"])
    with pytest.raises(PermissionError):
        oauth.refresh(t["refresh_token"])  # reutilização do refresh antigo
    assert oauth.refresh(t2["refresh_token"])["access_token"]


def test_expired_token_is_refreshed_and_never_exposed(client, auth):
    with session_scope() as db:
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.channel == "SHOPEE")).scalars().first()
        acc.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        old = acc.access_token_ciphertext
    with session_scope() as db:
        tok = channels.valid_access_token(db, "SHOPEE")
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.channel == "SHOPEE")).scalars().first()
        assert (
            tok.startswith("SIM-AT-")
            and acc.access_token_ciphertext != old
            and "SIM-AT" not in acc.access_token_ciphertext
        )
    listed = client.get("/v1/channels/accounts", headers=auth("viewer")).json()["items"]
    assert listed and all("token" not in k for row in listed for k in row)


def test_revoked_refresh_marks_reauth_and_execution_fails_typed(client, ctx):  # noqa: F811
    op, owner = ctx["op"], ctx["owner"]
    map_cat(client, op, "SHOPEE", ctx["product"]["category"], "100123")
    x = prepare(client, ctx, channel="SHOPEE")
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op).json()
    decide(client, owner, prop)
    with session_scope() as db:
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.channel == "SHOPEE")).scalars().first()
        acc.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        acc.refresh_token_ciphertext = channels._enc("SIM-RT-revogado")
    try:
        drain()
        s = client.get(f"/v1/approvals/{prop['id']}", headers=owner).json()
        assert s["status"] == "FAILED" and s["job"]["error_kind"] == "TOKEN_EXPIRED"
        with session_scope() as db:
            assert (
                db.execute(select(ChannelAccount.status).where(ChannelAccount.channel == "SHOPEE")).scalar()
                == "REAUTH_REQUIRED"
            )
    finally:
        client.post("/v1/sim/channels/SHOPEE/connect", headers=op)


# ───────────── Notificações do Mercado Livre ─────────────
def note(**over):
    n = {
        "_id": uuid.uuid4().hex,
        "resource": "/orders/1",
        "user_id": 1,
        "topic": "orders_v2",
        "application_id": get_settings().ml_application_id,
        "attempts": 1,
        "sent": "2026-10-24T10:00:00Z",
        "received": "2026-10-24T10:00:01Z",
    }
    n.update(over)
    return n


def test_ml_notification_validation_and_dedupe(client):
    assert client.post("/v1/webhooks/mercadolivre", json=note(application_id="OUTRA")).status_code == 403
    assert client.post("/v1/webhooks/mercadolivre", json=note(topic="desconhecido")).status_code == 400
    assert client.post("/v1/webhooks/mercadolivre", json=note(resource="orders/1")).status_code == 400
    n = note(_id=None, resource=f"/orders/{uuid.uuid4().int % 10**9}")
    n.pop("_id")
    assert client.post("/v1/webhooks/mercadolivre", json=n).json()["duplicate"] is False
    assert client.post("/v1/webhooks/mercadolivre", json={**n, "attempts": 2}).json()["duplicate"] is True


def test_ml_order_flows_from_notification_to_order(client, ctx):  # noqa: F811
    op, owner = ctx["op"], ctx["owner"]
    map_cat(client, op, "MERCADO_LIVRE", ctx["product"]["category"], "SIM-MLB1234")
    x = prepare(client, ctx, channel="MERCADO_LIVRE")
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op).json()
    decide(client, owner, prop)
    drain()
    r = client.post("/v1/sim/mercadolivre/orders", headers=op, json={"listing_id": x["id"], "qty": 2})
    assert r.status_code == 201
    drain()
    with session_scope() as db:
        o = db.execute(
            select(Order).where(
                Order.channel == "MERCADO_LIVRE", Order.external_id == r.json()["resource"].split("/")[-1]
            )
        ).scalar_one()
        assert o.payment_status == "CAPTURED" and o.status == "FISCAL_PENDING" and o.total_minor == 2 * x["price_minor"]
        ev = db.execute(
            select(WebhookEvent).where(WebhookEvent.delivery_id == r.json()["notification_id"])
        ).scalar_one()
        assert ev.status == "APPLIED"
    missing = note(resource="/orders/999999999")
    client.post("/v1/webhooks/mercadolivre", json=missing)
    drain()
    with session_scope() as db:
        assert (
            db.execute(select(WebhookEvent.status).where(WebhookEvent.delivery_id == missing["_id"])).scalar()
            == "REJECTED"
        )


def test_oauth_state_is_shared_across_processes():
    """Seed, API e worker são processos distintos: um refresh emitido por um precisa valer no outro."""
    issued = mp.MockMLOAuth().issue()
    assert mp.MockMLOAuth().refresh(issued["refresh_token"])["access_token"].startswith("SIM-AT-")
    with pytest.raises(PermissionError):
        mp.MockMLOAuth().refresh(issued["refresh_token"])
