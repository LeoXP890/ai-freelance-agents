"""Cliente Mercado Livre REAL (COM-005 / gate CH-ML) com a API do ML falsa em memória.

Só envia em LIVE + conta real conectada por OAuth + gate aprovado; não duplica item em retry (procura pelo SKU do
vendedor antes de criar); atualização manda só campos que o ML aceita mudar; timeout depois do envio vira UNKNOWN e
a reconciliação consulta o ML; erros HTTP viram tipos certos. Conexão OAuth com PKCE (verificador só no servidor,
state de uso único e com validade), renovação real serializada e segredo da aplicação proibido em DRY_RUN."""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import types
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from aicommerce.commerce import channels
from aicommerce.config import LiveCredentialsInDryRun, Settings
from aicommerce.db import session_scope
from aicommerce.models import ChannelAccount, OAuthState
from aicommerce.providers import marketplaces as mp
from aicommerce.providers.contracts import Capability, ErrorKind, ResultStatus
from aicommerce.providers.registry import resolve
from tests.test_listings import cmd

API = mp.ML_API
UID = "777001"


def item(sku="SKU-ML-1", **kw):
    base = mp.to_ml_item(
        title="Garrafa térmica 500 ml",
        category_id="MLB1234",
        price_minor=8990,
        stock_qty=12,
        images=["https://cdn.exemplo/garrafa.jpg"],
        sku=sku,
        brand="Marca",
        gtin=None,
    )
    return {**base, **kw}


class FakeML:
    """Items API do ML em memória (busca por seller_sku, GET/POST/PUT de item)."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.fail: dict[str, object] = {}
        self.n = 1000

    def __call__(self, method, url, headers, body, timeout, form=False):
        self.calls.append((method, url, body, headers))
        assert headers["Authorization"] == "Bearer AT-REAL"
        f = self.fail.get(method)
        if isinstance(f, Exception):
            raise f
        if isinstance(f, tuple):
            return f
        path = url.removeprefix(API)
        if method == "GET" and path.startswith(f"/users/{UID}/items/search?seller_sku="):
            sku = path.split("seller_sku=")[1]
            return 200, {"results": [i["id"] for i in self.items.values() if i["seller_custom_field"] == sku]}
        iid = path.removeprefix("/items/").split("?")[0]
        if method == "GET":
            return (200, self.items[iid]) if iid in self.items else (404, {"message": "item not found"})
        if method == "POST" and path == "/items":
            self.n += 1
            iid = f"MLB{self.n}"
            self.items[iid] = {**body, "id": iid, "status": "active", "permalink": f"https://ml/{iid}"}
            return 201, self.items[iid]
        if method == "PUT":
            if iid not in self.items:
                return 404, {"message": "item not found"}
            self.items[iid].update(body)
            return 200, self.items[iid]
        return 405, {}


def ml(api, gate=True, connected=True):
    return mp.MLRestProvider(gate_approved=gate, connected=connected, token_source=lambda: ("AT-REAL", UID), http=api)


def live(op, payload, key=None):
    c = cmd(op, payload, mode="LIVE")
    return dataclasses.replace(c, idempotency_key=key) if key else c


# ── capacidade e barreiras ──
def test_capability_requires_live_connected_account_and_gate():
    api = FakeML()
    assert ml(api).capability("publish_listing", "DRY_RUN") is Capability.UNAVAILABLE
    assert ml(api, connected=False).capability("publish_listing", "LIVE") is Capability.UNAVAILABLE
    assert ml(api, gate=False).capability("publish_listing", "LIVE") is Capability.BLOCKED_BY_POLICY
    assert ml(api).capability("publish_listing", "LIVE") is Capability.SUPPORTED
    r = ml(api, gate=False).execute(live("publish_listing", item()))
    assert r.error_kind is ErrorKind.POLICY_DENIED and api.calls == []  # sem gate: nenhuma chamada ao ML


def test_registry_live_ml_follows_account_and_gate():
    assert resolve("PUBLISH_LISTING", "LIVE", "MERCADO_LIVRE").capability is Capability.UNAVAILABLE
    r = resolve("PUBLISH_LISTING", "LIVE", "MERCADO_LIVRE", live_accounts=frozenset({"MERCADO_LIVRE"}))
    assert r.capability is Capability.BLOCKED_BY_POLICY and "CH-ML" in r.reason
    r = resolve("PUBLISH_LISTING", "LIVE", "MERCADO_LIVRE", frozenset({"CH-ML"}), frozenset({"MERCADO_LIVRE"}))
    assert r.capability is Capability.SUPPORTED and r.provider.name == "mercadolivre-rest"


# ── publicar / replay / atualizar / pausar ──
def test_publish_then_replay_does_not_duplicate():
    api = FakeML()
    r = ml(api).execute(live("publish_listing", item()))
    assert r.status is ResultStatus.SUCCEEDED and r.provider_ref in api.items
    again = ml(api).execute(live("publish_listing", item()))
    assert again.status is ResultStatus.SUCCEEDED and again.provider_ref == r.provider_ref
    assert again.detail["replayed"] is True and len(api.items) == 1


def test_update_sends_only_mutable_fields_and_pause_sets_paused():
    api = FakeML()
    ref = ml(api).execute(live("publish_listing", item())).provider_ref
    up = ml(api).execute(live("update_listing", item(price=79.9, title="Outro título")))
    assert up.status is ResultStatus.SUCCEEDED
    put = next(c for c in api.calls if c[0] == "PUT")
    assert set(put[2]) == set(mp.MLRestProvider.UPDATABLE)  # título/categoria/tipo não vão no PUT
    assert api.items[ref]["price"] == 79.9 and api.items[ref]["title"] == "Garrafa térmica 500 ml"
    p = ml(api).execute(live("pause_listing", {"channel": "MERCADO_LIVRE", "external_id": ref, "reason": "x"}))
    assert p.status is ResultStatus.SUCCEEDED and api.items[ref]["status"] == "paused"


def test_update_of_missing_item_is_rejected_without_creating():
    api = FakeML()
    r = ml(api).execute(live("update_listing", item(sku="SKU-SUMIU")))
    assert r.error_kind is ErrorKind.REJECTED and api.items == {}


def test_simulated_category_and_out_of_contract_never_reach_ml():
    api = FakeML()
    r = ml(api).execute(live("publish_listing", item(category_id="SIM-MLB1")))
    assert r.error_kind is ErrorKind.VALIDATION and "categoria simulada" in r.detail["reason"]
    r = ml(api).execute(live("publish_listing", {"title": "x"}))
    assert r.error_kind is ErrorKind.VALIDATION
    assert not [c for c in api.calls if c[0] in ("POST", "PUT")]


# ── erros e incerteza ──
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ErrorKind.VALIDATION),
        (401, ErrorKind.TOKEN_EXPIRED),
        (403, ErrorKind.REJECTED),
        (429, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
    ],
)
def test_http_errors_are_typed(status, kind):
    api = FakeML()
    api.fail["POST"] = (
        status,
        {"message": "body invalid", "cause": [{"code": "item.price.invalid", "message": "preço inválido"}]},
    )
    r = ml(api).execute(live("publish_listing", item()))
    assert r.status is (ResultStatus.UNKNOWN if status >= 500 else ResultStatus.FAILED)
    assert r.error_kind is kind and r.detail["http"] == status
    if status == 400:
        assert "preço inválido" in r.detail["reason"]


def test_timeout_after_send_is_unknown_and_reconcile_finds_the_item():
    api = FakeML()
    key = f"listing:{uuid.uuid4()}:v1:publish_listing"
    orig = api.__call__

    def lost_response(method, url, headers, body, timeout, form=False):
        if method == "POST":
            orig(method, url, headers, body, timeout)  # o ML criou, mas a resposta se perdeu
            raise TimeoutError
        return orig(method, url, headers, body, timeout)

    p = ml(lost_response)
    r = p.execute(live("publish_listing", item(), key))
    assert r.status is ResultStatus.UNKNOWN and r.error_kind is ErrorKind.TIMEOUT
    found = p.lookup(key, item())
    assert found is not None and found.status is ResultStatus.SUCCEEDED and found.provider_ref in api.items


def test_timeout_before_send_is_transient_and_lookup_absent_is_none():
    api = FakeML()
    api.fail["GET"] = TimeoutError()
    r = ml(api).execute(live("publish_listing", item()))
    assert r.status is ResultStatus.FAILED and r.error_kind is ErrorKind.TRANSIENT
    assert ml(FakeML()).lookup("k", item()) is None


def test_reconcile_update_requires_applied_values_and_pause_requires_paused():
    api = FakeML()
    ref = ml(api).execute(live("publish_listing", item())).provider_ref
    p = ml(api)
    assert p.lookup("listing:x:v2:update_listing_material", item(price=1.0)) is None  # não aplicado: reaplicar
    assert p.lookup("listing:x:v2:update_listing_material", item()) is not None
    pause = {"channel": "MERCADO_LIVRE", "external_id": ref, "reason": "x"}
    assert p.lookup("pause", pause) is None
    api.items[ref]["status"] = "paused"
    assert p.lookup("pause", pause).provider_ref == ref


def test_token_failure_is_typed_and_sends_nothing():
    api = FakeML()

    def reauth():
        raise channels.TokenUnavailable("refresh recusado")

    def down():
        raise channels.TokenRefreshTransient("ML fora do ar")

    for src, kind in ((reauth, ErrorKind.TOKEN_EXPIRED), (down, ErrorKind.TRANSIENT)):
        p = mp.MLRestProvider(gate_approved=True, connected=True, token_source=src, http=api)
        assert p.execute(live("publish_listing", item())).error_kind is kind
    assert api.calls == []


# ── OAuth real (PKCE) e renovação ──
@pytest.fixture
def live_app(monkeypatch):
    fake = types.SimpleNamespace(
        is_dry_run=False,
        ml_application_id="123456",
        ml_client_secret=SecretStr("segredo-app"),
        ml_redirect_uri="https://api.loja.exemplo/api/v1/channels/MERCADO_LIVRE/oauth/callback",
    )
    monkeypatch.setattr(channels, "get_settings", lambda: fake)
    yield fake
    with session_scope() as db:
        db.execute(delete(ChannelAccount).where(ChannelAccount.is_simulated.is_(False)))
        db.execute(delete(OAuthState))


class TokenEndpoint:
    def __init__(self):
        self.calls: list[dict] = []
        self.reply: tuple[int, dict] | Exception | None = None
        self.n = 0

    def __call__(self, method, url, headers, body, timeout, form=False):
        assert url == mp.ML_TOKEN_URL and form and method == "POST"
        self.calls.append(body)
        if isinstance(self.reply, Exception):
            raise self.reply
        if self.reply:
            return self.reply
        self.n += 1
        return 200, {
            "access_token": f"AT-{self.n}",
            "refresh_token": f"RT-{self.n}",
            "expires_in": 21600,
            "user_id": int(UID),
            "scope": "offline_access read write",
        }


def start(db):
    url = channels.oauth_start(db, "MERCADO_LIVRE", actor="owner-teste")
    q = parse_qs(urlparse(url).query)
    return q["state"][0], q["code_challenge"][0], q


def test_oauth_start_keeps_verifier_on_server_and_finish_connects(live_app, monkeypatch):
    ep = TokenEndpoint()
    monkeypatch.setattr(channels, "_http", ep)
    with session_scope() as db:
        state, challenge, q = start(db)
        assert q["client_id"] == ["123456"] and q["code_challenge_method"] == ["S256"]
        assert "code_verifier" not in q
        row = db.get(OAuthState, hashlib.sha256(state.encode()).hexdigest())
        assert row is not None and "verifier" not in row.code_verifier_ciphertext
    with session_scope() as db:
        acc = channels.oauth_finish(db, code="TG-abc", state=state)
        assert acc.status == "CONNECTED" and not acc.is_simulated and acc.external_account_id == UID
        assert "AT-1" not in (acc.access_token_ciphertext or "")  # tokens cifrados no banco
    sent = ep.calls[0]
    assert sent["grant_type"] == "authorization_code" and sent["redirect_uri"] == live_app.ml_redirect_uri
    digest = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert digest == challenge  # o verificador guardado corresponde ao desafio enviado ao ML
    with session_scope() as db:
        assert channels.live_accounts(db) == frozenset({"MERCADO_LIVRE"})
        assert channels.live_token("MERCADO_LIVRE") == ("AT-1", UID)
        with pytest.raises(channels.OAuthError, match="já usado"):
            channels.oauth_finish(db, code="TG-abc", state=state)  # state de uso único


def test_oauth_state_expires_and_ml_refusal_is_reported(live_app, monkeypatch):
    ep = TokenEndpoint()
    monkeypatch.setattr(channels, "_http", ep)
    with session_scope() as db:
        state, _, _ = start(db)
    with session_scope() as db, pytest.raises(channels.OAuthError, match="10 minutos"):
        channels.oauth_finish(db, code="c", state=state, now=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=11))
    assert ep.calls == []  # expirado: nem chega ao ML
    ep.reply = (400, {"error": "invalid_grant"})
    with session_scope() as db:
        state, _, _ = start(db)
    with session_scope() as db, pytest.raises(channels.OAuthError, match="invalid_grant"):
        channels.oauth_finish(db, code="c", state=state)


def connect_real(monkeypatch):
    ep = TokenEndpoint()
    monkeypatch.setattr(channels, "_http", ep)
    with session_scope() as db:
        state, _, _ = start(db)
    with session_scope() as db:
        channels.oauth_finish(db, code="c", state=state)
    return ep


def test_real_refresh_rotates_tokens_reauth_on_invalid_grant_and_keeps_account_when_ml_is_down(live_app, monkeypatch):
    ep = connect_real(monkeypatch)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=6)
    with session_scope() as db:
        assert channels.valid_access_token(db, "MERCADO_LIVRE", later) == "AT-2"
    assert ep.calls[-1]["grant_type"] == "refresh_token" and ep.calls[-1]["refresh_token"] == "RT-1"
    ep.reply = TimeoutError()
    much_later = later + dt.timedelta(hours=6)
    with session_scope() as db, pytest.raises(channels.TokenRefreshTransient):
        channels.valid_access_token(db, "MERCADO_LIVRE", much_later)
    with session_scope() as db:
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.is_simulated.is_(False))).scalar_one()
        assert acc.status == "CONNECTED"  # indisponibilidade não derruba a conexão
    ep.reply = (400, {"error": "invalid_grant"})
    with session_scope() as db, pytest.raises(channels.TokenUnavailable, match="invalid_grant"):
        channels.valid_access_token(db, "MERCADO_LIVRE", much_later)
    with session_scope() as db:
        acc = db.execute(select(ChannelAccount).where(ChannelAccount.is_simulated.is_(False))).scalar_one()
        assert acc.status == "REAUTH_REQUIRED"


def test_oauth_start_refuses_in_dry_run_and_without_app(live_app, monkeypatch):
    with session_scope() as db:
        live_app.is_dry_run = True
        with pytest.raises(channels.OAuthError, match="DRY_RUN"):
            channels.oauth_start(db, "MERCADO_LIVRE", actor="x")
        live_app.is_dry_run, live_app.ml_client_secret = False, None
        with pytest.raises(channels.OAuthError, match="não configurada"):
            channels.oauth_start(db, "MERCADO_LIVRE", actor="x")
        with pytest.raises(channels.OAuthError, match="SHOPEE"):
            channels.oauth_start(db, "SHOPEE", actor="x")


def test_oauth_routes_require_mfa_and_callback_rejects_unknown_state(client, auth):
    owner = auth("owner")
    r = client.post("/v1/channels/MERCADO_LIVRE/oauth/start", headers=owner)
    assert r.status_code == 409 and "DRY_RUN" in r.json()["detail"]["message"]  # DRY_RUN: conexão real indisponível
    assert client.post("/v1/channels/MERCADO_LIVRE/oauth/start", headers=auth("operator")).status_code == 403
    r = client.get("/v1/channels/MERCADO_LIVRE/oauth/callback", params={"code": "c", "state": "forjado"})
    assert r.status_code == 400 and "já usado" in r.text and "no-store" in r.headers["cache-control"]
    r = client.get("/v1/channels/MERCADO_LIVRE/oauth/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and "access_denied" in r.text


def test_ml_app_secret_forbidden_in_dry_run(monkeypatch):
    with pytest.raises(LiveCredentialsInDryRun):
        Settings(mode="DRY_RUN", ml_client_secret="segredo")
