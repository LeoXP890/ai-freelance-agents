"""Adaptador WooCommerce REAL (COM-004 / gate CH-WOO) com HTTP falso: só envia em LIVE + loja configurada + gate
aprovado; não duplica produto em retry (SKU + meta aic_listing_id); timeout depois do envio vira UNKNOWN e a
reconciliação consulta a loja; erros HTTP viram tipos certos. Credencial da loja nunca em DRY_RUN."""

from __future__ import annotations

import base64

import pytest

from aicommerce.providers.contracts import Capability, ErrorKind, ResultStatus
from aicommerce.providers.registry import resolve
from aicommerce.providers.woocommerce import WooCommerceRestProvider
from tests.test_listings import cmd, woo_payload

BASE = "https://loja.exemplo.com.br"


class FakeStore:
    """Loja WooCommerce em memória que responde como a REST API v3."""

    def __init__(self):
        self.products: dict[int, dict] = {}
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.next_id = 100
        self.fail: dict[str, object] = {}

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, body, headers))
        path = url.removeprefix(BASE)
        f = self.fail.get(method)
        if isinstance(f, Exception):
            raise f
        if isinstance(f, int):
            return f, {"code": "woocommerce_rest_error", "message": "erro"}
        if method == "GET" and path.startswith("/wp-json/wc/v3/products?sku="):
            sku = path.split("sku=")[1].split("&")[0]
            return 200, [p for p in self.products.values() if p["sku"] == sku]
        if method == "GET":
            pid = int(path.rsplit("/", 1)[1])
            return (
                (200, self.products[pid])
                if pid in self.products
                else (404, {"code": "woocommerce_rest_product_invalid_id"})
            )
        if method == "POST":
            pid, self.next_id = self.next_id, self.next_id + 1
            self.products[pid] = {**body, "id": pid}
            return 201, self.products[pid]
        if method == "PUT":
            pid = int(path.rsplit("/", 1)[1])
            if pid not in self.products:
                return 404, {"code": "woocommerce_rest_product_invalid_id", "message": "ID inválido"}
            self.products[pid].update(body)
            return 200, self.products[pid]
        return 405, {}


def woo(store, gate=True, **kw):
    return WooCommerceRestProvider(BASE, "ck_teste", "cs_teste", gate_approved=gate, http=store, **kw)


def test_capability_matrix():
    s = FakeStore()
    assert woo(s).capability("publish_listing", "LIVE") is Capability.SUPPORTED
    assert woo(s, gate=False).capability("publish_listing", "LIVE") is Capability.BLOCKED_BY_POLICY
    assert woo(s).capability("publish_listing", "DRY_RUN") is Capability.UNAVAILABLE  # nunca em simulação
    assert (
        WooCommerceRestProvider(BASE, "", "", gate_approved=True).capability("publish_listing", "LIVE")
        is Capability.UNAVAILABLE
    )
    assert woo(s).capability("refund_order", "LIVE") is Capability.UNAVAILABLE
    r = woo(s, gate=False).execute(cmd("publish_listing", woo_payload(), mode="LIVE"))
    assert r.error_kind is ErrorKind.POLICY_DENIED and s.calls == []  # gate não aprovado: nada sai para a loja


def test_publish_creates_with_auth_and_idempotency_meta_then_replay_does_not_duplicate():
    s = FakeStore()
    c = cmd("publish_listing", woo_payload(), mode="LIVE")
    r = woo(s).execute(c)
    assert r.status is ResultStatus.SUCCEEDED and r.provider_ref == "100" and len(s.products) == 1
    method, url, body, headers = s.calls[-1]
    assert (method, url) == ("POST", BASE + "/wp-json/wc/v3/products")
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"ck_teste:cs_teste").decode()
    assert {"key": "aic_idempotency_key", "value": c.idempotency_key} in body["meta_data"]
    again = woo(s).execute(cmd("publish_listing", woo_payload(), mode="LIVE"))  # retry/replay do mesmo anúncio
    assert again.status is ResultStatus.SUCCEEDED and again.provider_ref == "100" and again.detail["replayed"]
    assert len(s.products) == 1


def test_sku_taken_by_other_product_is_rejected():
    s = FakeStore()
    woo(s).execute(cmd("publish_listing", woo_payload(), mode="LIVE"))
    other = woo_payload()
    other["meta_data"] = [{"key": "aic_listing_id", "value": "outro-anuncio"}]
    r = woo(s).execute(cmd("publish_listing", other, mode="LIVE"))
    assert r.error_kind is ErrorKind.REJECTED and "já existe" in r.detail["reason"] and len(s.products) == 1


def test_update_finds_product_by_sku_and_pause_sets_draft():
    s = FakeStore()
    woo(s).execute(cmd("publish_listing", woo_payload(), mode="LIVE"))
    r = woo(s).execute(cmd("update_listing", woo_payload(regular_price="79.90"), mode="LIVE"))
    assert r.status is ResultStatus.SUCCEEDED and s.products[100]["regular_price"] == "79.90"
    p = woo(s).execute(
        cmd("pause_listing", {"channel": "WOOCOMMERCE", "external_id": "100", "reason": "x"}, mode="LIVE")
    )
    assert p.status is ResultStatus.SUCCEEDED and s.products[100]["status"] == "draft"
    gone = woo(FakeStore()).execute(cmd("update_listing", woo_payload(), mode="LIVE"))
    assert gone.error_kind is ErrorKind.REJECTED and "não encontrado" in gone.detail["reason"]


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (400, ErrorKind.VALIDATION),
        (401, ErrorKind.REJECTED),
        (403, ErrorKind.REJECTED),
        (429, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
    ],
)
def test_http_errors_are_typed(code, kind):
    s = FakeStore()
    s.fail["POST"] = code
    r = woo(s).execute(cmd("publish_listing", woo_payload(), mode="LIVE"))
    assert r.status is ResultStatus.FAILED and r.error_kind is kind and r.detail["http"] == code


def test_timeout_after_send_is_unknown_and_reconcile_finds_effect():
    s = FakeStore()
    payload = woo_payload()
    c = cmd("publish_listing", payload, mode="LIVE")
    real = s.__call__

    def lossy(method, url, headers, body, timeout):  # a loja grava, mas a resposta se perde
        out = real(method, url, headers, body, timeout)
        if method == "POST":
            raise TimeoutError("resposta perdida")
        return out

    r = woo(lossy).execute(c)
    assert r.status is ResultStatus.UNKNOWN and r.error_kind is ErrorKind.TIMEOUT and len(s.products) == 1
    found = woo(s).lookup(c.idempotency_key, payload)
    assert found is not None and found.status is ResultStatus.SUCCEEDED and found.provider_ref == "100"
    assert woo(FakeStore()).lookup(c.idempotency_key, payload) is None  # efeito ausente: executor decide repetir
    s.fail["GET"] = TimeoutError("caiu")
    assert woo(s).lookup(c.idempotency_key, payload) is None


def test_timeout_before_send_is_plain_transient():
    s = FakeStore()
    s.fail["GET"] = TimeoutError("sem rede")
    r = woo(s).execute(cmd("publish_listing", woo_payload(), mode="LIVE"))
    assert r.status is ResultStatus.FAILED and r.error_kind is ErrorKind.TRANSIENT


def test_registry_uses_gate_state():
    blocked = resolve("PUBLISH_LISTING", "LIVE", "WOOCOMMERCE", frozenset({"CH-WOO"}))
    assert blocked.capability is Capability.UNAVAILABLE  # ambiente de teste sem loja configurada


def test_settings_guards():
    from aicommerce.config import LiveCredentialsInDryRun, Settings

    with pytest.raises(LiveCredentialsInDryRun):
        Settings(woo_consumer_key="ck_real", woo_consumer_secret="cs_real")
    with pytest.raises(ValueError, match="https"):
        Settings(mode="LIVE", jwt_secret="x" * 48, woo_base_url="http://loja.com", woo_consumer_key="ck")
