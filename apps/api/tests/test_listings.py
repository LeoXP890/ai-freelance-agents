import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import func, select

from aicommerce.commerce import webhooks
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.governance import executor
from aicommerce.models import ChannelListing, ListingVersion, Supplier, SupplierOffer, WebhookEvent
from aicommerce.providers.contracts import Capability, CommandEnvelope, ErrorKind
from aicommerce.providers.registry import resolve
from aicommerce.providers.woocommerce import (
    MockWooCommerceProvider,
    WooCommerceRestProvider,
    sign,
    to_woo_product,
    validate_product,
)
from tests.test_approvals import decide
from tests.test_suppliers import approve_supplier, make_ready, mk_product, mk_supplier


@pytest.fixture
def ctx(client, auth):
    op, owner = auth("operator"), auth("owner")
    prod = mk_product(client, op)
    sup = mk_supplier(client, op)
    vid = prod["variants"][0]["id"]
    offer = make_ready(client, op, owner, sup, vid)
    approve_supplier(client, owner, sup["id"])
    return {"op": op, "owner": owner, "variant_id": vid, "offer": offer, "supplier": sup, "product": prod}


def prepare(client, c, channel="WOOCOMMERCE", **over):
    body = {"variant_id": c["variant_id"], "offer_id": c["offer"]["id"], "shipping_minor": 1500, **over}
    r = client.post(f"/v1/channels/{channel}/listings/prepare", headers=c["op"], json=body)
    assert r.status_code == 200, r.text
    return r.json()


def drain():
    while executor.execute_next() or executor.reconcile_next():
        pass


def codes(listing):
    return {i["code"] for i in listing["compliance"]}


def test_prepare_woo_uses_suggested_price_and_valid_contract(client, ctx):
    x = prepare(client, ctx)
    q = x["pricing"]["quote"]
    assert x["status"] == "DRAFT" and x["version"] == 1 and x["blockers"] == []
    assert x["price_minor"] == q["suggested_minor"] and q["breakdown"]["contribution_minor"] > 0
    assert validate_product(x["channel_payload"]) == []
    assert x["channel_payload"]["regular_price"] == f"{x['price_minor'] / 100:.2f}"
    assert {"aic_listing_id", "aic_simulated"} <= {m["key"] for m in x["channel_payload"]["meta_data"]}
    assert "PRICING" in codes(x)  # tarifas/tributos fictícios sinalizados


def test_full_flow_prepare_propose_approve_publish(client, ctx):
    x = prepare(client, ctx)
    r = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=ctx["op"])
    assert r.status_code == 201, r.text
    prop = r.json()
    assert prop["status"] == "PENDING_APPROVAL" and prop["subject_version"] == 1
    assert any(e.startswith("listing_version:") for e in prop["evidence_refs"])
    assert any(e.startswith("supplier_document:") for e in prop["evidence_refs"])
    assert any("equivalente LIVE seria DENY" in r for r in prop["policy_reasons"])
    listing = client.get(f"/v1/listings/{x['id']}", headers=ctx["op"]).json()
    assert listing["status"] == "PENDING_APPROVAL"
    assert decide(client, ctx["owner"], prop).status_code == 200
    drain()
    listing = client.get(f"/v1/listings/{x['id']}", headers=ctx["op"]).json()
    assert listing["status"] == "PUBLISHED" and listing["external_id"].startswith("SIM-mock-commerce-woocommerce")
    assert listing["versions"][0]["payload_sha256"] == prop["payload_sha256"]
    # nova versão de anúncio publicado vira UPDATE_LISTING_MATERIAL
    prepare(client, ctx, price_minor=listing["price_minor"] + 500)
    upd = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=ctx["op"]).json()
    assert upd["action_type"] == "UPDATE_LISTING_MATERIAL" and upd["subject_version"] == 2


def test_reprepare_expires_pending_proposal(client, ctx):
    x = prepare(client, ctx)
    p1 = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=ctx["op"]).json()
    x2 = prepare(client, ctx, price_minor=x["price_minor"] + 100)
    assert x2["version"] == 2
    assert client.get(f"/v1/approvals/{p1['id']}", headers=ctx["owner"]).json()["status"] == "EXPIRED"
    assert decide(client, ctx["owner"], p1).status_code == 409
    with session_scope() as db:
        n = db.execute(
            select(func.count()).select_from(ListingVersion).where(ListingVersion.listing_id == uuid.UUID(x["id"]))
        ).scalar_one()
        assert n == 2


def test_supplier_stock_and_cost_changes_require_reprepare(client, ctx):
    """Nova validação evita aprovar anúncio com dados obsoletos do Nexus."""
    prepared = prepare(client, ctx)
    offer_id = uuid.UUID(ctx["offer"]["id"])
    with session_scope() as db:
        offer = db.get(SupplierOffer, offer_id)
        offer.stock_qty = 0
    blocked = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert blocked.status_code == 409 and "OUT_OF_STOCK" in blocked.text

    with session_scope() as db:
        offer = db.get(SupplierOffer, offer_id)
        offer.stock_qty = 7
    blocked = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert blocked.status_code == 409 and "estoque" in blocked.text

    with session_scope() as db:
        offer = db.get(SupplierOffer, offer_id)
        offer.stock_qty = 50
        offer.cost_minor += 100
    blocked = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert blocked.status_code == 409 and "custo" in blocked.text

    refreshed = prepare(client, ctx)
    assert refreshed["version"] == 2
    proposed = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert proposed.status_code == 201 and proposed.json()["subject_version"] == 2


def test_supplier_suspension_or_expired_stock_rechecked_on_proposal(client, ctx):
    prepared = prepare(client, ctx)
    with session_scope() as db:
        sup = db.get(Supplier, uuid.UUID(ctx["supplier"]["id"]))
        sup.status = "SUSPENDED"
    r = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert r.status_code == 409 and "SUPPLIER_NOT_APPROVED" in r.text

    with session_scope() as db:
        sup = db.get(Supplier, uuid.UUID(ctx["supplier"]["id"]))
        sup.status = "APPROVED"
        offer = db.get(SupplierOffer, uuid.UUID(ctx["offer"]["id"]))
        offer.stock_observed_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
    r = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert r.status_code == 409 and "STOCK_STALE" in r.text


def test_executor_refuses_stale_stock_after_owner_approval(client, ctx):
    """A aprovação com MFA não congela a realidade logística enquanto o job aguarda."""
    prepared = prepare(client, ctx)
    reply = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert reply.status_code == 201, reply.text
    proposal = reply.json()
    assert decide(client, ctx["owner"], proposal).status_code == 200

    # Mudança real observada após a decisão e antes da chamada ao marketplace.
    with session_scope() as db:
        db.get(SupplierOffer, uuid.UUID(ctx["offer"]["id"])).stock_qty = 0
    drain()
    result = client.get(f"/v1/approvals/{proposal['id']}", headers=ctx["owner"]).json()
    assert result["status"] == "FAILED"
    assert result["job"]["error_kind"] == "POLICY_DENIED"
    listing = client.get(f"/v1/listings/{prepared['id']}", headers=ctx["op"]).json()
    assert listing["status"] == "ERROR" and not listing["external_id"]


def test_real_publication_requires_verified_fees_and_taxes(client, ctx, monkeypatch):
    """Defesa adicional: uma cotação de simulação não pode virar anúncio LIVE."""
    from types import SimpleNamespace

    from aicommerce.commerce import listings

    prepared = prepare(client, ctx)
    monkeypatch.setattr(listings, "get_settings", lambda: SimpleNamespace(is_dry_run=False))
    response = client.post(f"/v1/listings/{prepared['id']}/propose-publish", headers=ctx["op"])
    assert response.status_code == 409 and "não homologados" in response.text


def test_negative_margin_blocks_proposal(client, ctx):
    x = prepare(client, ctx, price_minor=1000)
    assert "NEGATIVE_MARGIN" in codes(x)
    r = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=ctx["op"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "COMPLIANCE_BLOCKED"


def test_unapproved_supplier_stale_stock_and_fulfillment_blockers(client, auth, ctx):
    op = ctx["op"]
    sup = mk_supplier(client, op, origin="INTERNATIONAL")
    offer = client.post(
        f"/v1/suppliers/{sup['id']}/offers",
        headers=op,
        json={
            "variant_id": ctx["variant_id"],
            "cost_minor": 1500,
            "lead_time_days": 20,
            "stock_qty": 10,
            "source_ref": "cotacao:x",
        },
    ).json()
    with session_scope() as db:
        db.get(SupplierOffer, uuid.UUID(offer["id"])).stock_observed_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
    cursor = client.get("/v1/agent-presence/snapshot", headers=op).json()["as_of_seq"]
    x = prepare(client, {**ctx, "offer": offer}, channel="MERCADO_LIVRE")
    assert {"SUPPLIER_NOT_APPROVED", "STOCK_STALE", "FULFILLMENT_MISMATCH", "CATEGORY_UNMAPPED"} <= codes(x)
    evs = client.get(f"/v1/office/events?after_seq={cursor}", headers=op).json()["items"]
    assert any(e["type"] == "supplier.stock.stale" and e["agent_id"] == "nexus" for e in evs)


def test_regulated_without_docs_blocked(client, ctx):
    r = client.post(
        "/v1/products",
        headers=ctx["op"],
        json={
            "sku_base": f"REG-{uuid.uuid4().hex[:5]}",
            "title": "Suplemento",
            "category": "Saúde",
            "regulated": True,
            "variants": [
                {
                    "sku": f"REGV-{uuid.uuid4().hex[:5]}",
                    "weight_g": 100,
                    "length_mm": 50,
                    "width_mm": 50,
                    "height_mm": 100,
                }
            ],
        },
    ).json()
    offer = client.post(
        f"/v1/suppliers/{ctx['supplier']['id']}/offers",
        headers=ctx["op"],
        json={
            "variant_id": r["variants"][0]["id"],
            "cost_minor": 2000,
            "lead_time_days": 2,
            "stock_qty": 10,
            "source_ref": "cotacao:x",
        },
    ).json()
    x = prepare(client, {**ctx, "variant_id": r["variants"][0]["id"], "offer": offer})
    assert "REGULATED_WITHOUT_DOCS" in codes(x)


def test_marketplace_mock_publishes_with_ml_contract(client, ctx):
    client.put(
        "/v1/channels/MERCADO_LIVRE/categories",
        headers=ctx["op"],
        json={"internal_category": ctx["product"]["category"], "external_category_id": "SIM-MLB1234"},
    )
    x = prepare(client, ctx, channel="MERCADO_LIVRE")
    assert x["blockers"] == [] and "CATEGORY_UNVERIFIED" in codes(x)
    p = x["channel_payload"]
    assert p["currency_id"] == "BRL" and p["buying_mode"] == "buy_it_now" and p["category_id"] == "SIM-MLB1234"
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=ctx["op"]).json()
    decide(client, ctx["owner"], prop)
    drain()
    assert client.get(f"/v1/listings/{x['id']}", headers=ctx["op"]).json()["status"] == "PUBLISHED"


def test_permissions(client, auth, ctx):
    viewer = auth("viewer")
    body = {"variant_id": ctx["variant_id"], "offer_id": ctx["offer"]["id"]}
    assert client.post("/v1/channels/WOOCOMMERCE/listings/prepare", headers=viewer, json=body).status_code == 403
    assert client.get("/v1/listings", headers=viewer).status_code == 200


# ───────────── Contrato WooCommerce ─────────────
def woo_payload(**over):
    p = to_woo_product(
        listing_id="L1",
        title="Garrafa",
        description="d",
        sku="S1",
        price_minor=8990,
        stock_qty=5,
        weight_g=380,
        dims_mm=(75, 75, 260),
        images=[],
        category="Casa",
        simulated=True,
    )
    p.update(over)
    return p


def test_woo_mapping_matches_contract_and_units():
    p = woo_payload()
    assert validate_product(p) == []
    assert p["regular_price"] == "89.90" and p["weight"] == "0.380"
    assert p["dimensions"] == {"length": "7.5", "width": "7.5", "height": "26.0"}
    assert validate_product(woo_payload(regular_price="89.9"))
    assert validate_product(woo_payload(type="variable"))


def cmd(op, payload, mode="DRY_RUN"):
    return CommandEnvelope(
        request_id="r1",
        idempotency_key=f"k-{uuid.uuid4().hex}",
        mode=mode,
        capability=Capability.SUPPORTED,
        approval_token_ref="g1",
        expected_version=1,
        operation=op,
        payload=payload,
    )


def test_mock_woo_rejects_out_of_contract_payload():
    r = MockWooCommerceProvider().execute(cmd("publish_listing", {"title": "x", "price_minor": 1}))
    assert r.error_kind is ErrorKind.VALIDATION and "WooCommerce v3" in r.detail["reason"]
    ok = MockWooCommerceProvider().execute(cmd("publish_listing", woo_payload()))
    assert ok.provider_ref and ok.detail["simulated"]


def test_real_woo_client_without_store_credentials_never_sends():
    real = WooCommerceRestProvider("https://loja.exemplo.invalid", "", "")
    c = cmd("publish_listing", woo_payload(_simulate="x"))
    req = real.build_request(c)
    assert req["method"] == "POST" and req["url"] == "https://loja.exemplo.invalid/wp-json/wc/v3/products"
    assert "_simulate" not in req["json"] and req["headers"]["Idempotency-Key"] == c.idempotency_key
    assert real.build_request(cmd("pause_listing", {}), external_id="42")["json"] == {"status": "draft"}
    assert real.execute(c).error_kind is ErrorKind.UNSUPPORTED and real.lookup("k") is None
    live = resolve("PUBLISH_LISTING", "LIVE", "WOOCOMMERCE")  # sem loja configurada no ambiente de teste
    assert live.capability is Capability.UNAVAILABLE and "não configurada" in live.reason
    assert resolve("PUBLISH_LISTING", "LIVE", "SHOPEE").provider is None


# ───────────── Webhooks ─────────────
def post_hook(client, body: dict, *, delivery=None, topic="product.updated", secret=None, raw=None):
    raw = raw if raw is not None else json.dumps(body).encode()
    sig = sign(secret or get_settings().woo_webhook_secret, raw)
    headers = {
        "Content-Type": "application/json",
        "X-WC-Webhook-Signature": sig,
        "X-WC-Webhook-Delivery-ID": delivery or uuid.uuid4().hex,
        "X-WC-Webhook-Topic": topic,
        "X-WC-Webhook-Resource": "product",
    }
    return client.post("/v1/webhooks/woocommerce", content=raw, headers=headers)


def drain_hooks():
    while webhooks.process_next():
        pass


def hook_status(delivery):
    with session_scope() as db:
        ev = db.execute(select(WebhookEvent).where(WebhookEvent.delivery_id == delivery)).scalar_one()
        return ev.status, ev.result


def test_webhook_signature_dedupe_and_ping(client):
    d = uuid.uuid4().hex
    assert post_hook(client, {"id": 1}, delivery=d).json() == {"ack": True, "duplicate": False, "resource": "product"}
    assert post_hook(client, {"id": 1}, delivery=d).json()["duplicate"] is True
    with session_scope() as db:
        assert (
            db.execute(select(func.count()).select_from(WebhookEvent).where(WebhookEvent.delivery_id == d)).scalar_one()
            == 1
        )
    assert post_hook(client, {"id": 1}, secret="segredo-errado").status_code == 401
    raw = json.dumps({"id": 1}).encode()
    r = client.post(
        "/v1/webhooks/woocommerce",
        content=raw,
        headers={"X-WC-Webhook-Signature": sign(get_settings().woo_webhook_secret, raw)},
    )
    assert r.status_code == 400
    ping = client.post(
        "/v1/webhooks/woocommerce",
        content=b"webhook_id=17",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert ping.json()["ping"] is True


def test_webhook_out_of_order_and_status_sync(client, ctx):
    x = prepare(client, ctx)
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=ctx["op"]).json()
    decide(client, ctx["owner"], prop)
    drain()
    base = {"meta_data": [{"key": "aic_listing_id", "value": x["id"]}]}
    newer, older = uuid.uuid4().hex, uuid.uuid4().hex
    post_hook(client, {**base, "status": "draft", "date_modified_gmt": "2026-10-24T10:00:00"}, delivery=newer)
    post_hook(client, {**base, "status": "publish", "date_modified_gmt": "2026-10-24T09:00:00"}, delivery=older)
    drain_hooks()
    assert hook_status(newer)[0] == "APPLIED" and hook_status(older)[0] == "IGNORED_STALE"
    with session_scope() as db:
        assert db.get(ChannelListing, uuid.UUID(x["id"])).status == "PAUSED"


def test_webhook_invalid_order_rejected_and_unknown_product_ignored(client):
    o, u = uuid.uuid4().hex, uuid.uuid4().hex
    post_hook(client, {"id": 99}, delivery=o, topic="order.created")
    post_hook(client, {"id": 123456, "status": "publish", "date_modified_gmt": "2026-10-24T10:00:00"}, delivery=u)
    drain_hooks()
    assert hook_status(o)[0] == "REJECTED" and "itens" in hook_status(o)[1]
    assert hook_status(u)[0] == "IGNORED"
