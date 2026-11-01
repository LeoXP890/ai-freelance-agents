"""Computador lembrado: app desktop abre sem login após a primeira vez — sem enfraquecer a API."""

from sqlalchemy import select

from aicommerce.db import session_scope
from aicommerce.models import DeviceSession
from tests.conftest import login


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def remember(client, tok, name="MacBook do Leonardo"):
    r = client.post("/v1/auth/devices", headers=h(tok), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_device_login_replaces_password_and_keeps_mfa_level(client, make_user):
    owner = make_user("owner")
    dev = remember(client, login(client, owner))
    assert dev["device_token"].startswith("aicd_") and len(dev["device_token"]) > 40
    r = client.post("/v1/auth/device-login", json={"device_token": dev["device_token"]})
    assert r.status_code == 200
    me = client.get("/v1/auth/me", headers=h(r.json()["access_token"])).json()
    assert me["mfa"] is True and "approvals:decide" in me["permissions"]  # herdou a sessão com MFA
    with session_scope() as db:  # só o hash fica no banco
        stored = (
            db.execute(select(DeviceSession.token_sha256).where(DeviceSession.name == "MacBook do Leonardo"))
            .scalars()
            .all()
        )
    assert dev["device_token"] not in stored and all(len(x) == 64 for x in stored)


def test_revoked_or_unknown_device_is_refused(client, make_user):
    u = make_user("operator")
    tok = login(client, u)
    dev = remember(client, tok)
    assert client.post("/v1/auth/device-login", json={"device_token": "aicd_" + "x" * 43}).status_code == 401
    listed = client.get("/v1/auth/devices", headers=h(tok)).json()["items"]
    assert [d["name"] for d in listed] == ["MacBook do Leonardo"] and "device_token" not in str(listed)
    assert client.post(f"/v1/auth/devices/{dev['id']}/revoke", headers=h(tok)).json()["revoked"] is True
    assert client.post("/v1/auth/device-login", json={"device_token": dev["device_token"]}).status_code == 401


def test_password_change_invalidates_remembered_devices(client, make_user):
    u = make_user("operator")
    tok = login(client, u)
    dev = remember(client, tok)
    r = client.post(
        "/v1/auth/password",
        headers=h(tok),
        json={"current_password": u["password"], "new_password": "uma-senha-nova-bem-longa-2026"},
    )
    assert r.status_code == 200
    assert client.post("/v1/auth/device-login", json={"device_token": dev["device_token"]}).status_code == 401


def test_cannot_revoke_someone_elses_device(client, make_user):
    a, b = make_user("operator"), make_user("operator")
    dev = remember(client, login(client, a))
    assert client.post(f"/v1/auth/devices/{dev['id']}/revoke", headers=h(login(client, b))).status_code == 404
    assert client.post("/v1/auth/device-login", json={"device_token": dev["device_token"]}).status_code == 200


def test_device_limit(client, make_user):
    tok = login(client, make_user("viewer"))
    for i in range(10):
        remember(client, tok, f"pc-{i}")
    assert client.post("/v1/auth/devices", headers=h(tok), json={"name": "pc-11"}).status_code == 400
