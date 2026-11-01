import datetime as dt
import uuid

import pyotp

from aicommerce import manage
from aicommerce.db import session_scope
from aicommerce.identity import mfa, pii
from aicommerce.models import User


def new_owner():
    return manage._create_user(f"e-{uuid.uuid4().hex[:8]}@test.local", "Dono", ["owner"], None)


def login(client, u):
    return client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]})


def enroll(client, u):
    tok = login(client, u).json()["enrollment_token"]
    start = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()
    r = client.post(
        "/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": pyotp.TOTP(start["secret"]).now()}
    )
    assert r.status_code == 200, r.text
    return tok, start["secret"], r.json()["access_token"]


def code_at(secret, seconds_from_now):
    return pyotp.TOTP(secret).at(dt.datetime.now(dt.UTC) + dt.timedelta(seconds=seconds_from_now))


def test_first_login_requires_enrollment_and_token_is_not_access(client):
    u = new_owner()
    body = login(client, u).json()
    assert body["mfa_required"] and body["mfa_enrollment_required"]
    assert "access_token" not in body and "challenge_token" not in body
    h = {"Authorization": f"Bearer {body['enrollment_token']}"}
    assert client.get("/v1/auth/me", headers=h).status_code == 401
    assert (
        client.post(
            "/v1/auth/mfa/verify", json={"challenge_token": body["enrollment_token"], "code": "123456"}
        ).status_code
        == 401
    )


def test_start_returns_qr_and_regenerates_pending_secret(client):
    u = new_owner()
    tok = login(client, u).json()["enrollment_token"]
    a = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()
    assert a["qr_svg_data_uri"].startswith("data:image/svg+xml")
    assert a["otpauth_uri"].startswith("otpauth://totp/") and "issuer=AI%20Commerce" in a["otpauth_uri"]
    b = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()
    assert a["secret"] != b["secret"]
    # código do segredo antigo não confirma
    r = client.post(
        "/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": pyotp.TOTP(a["secret"]).now()}
    )
    assert r.status_code == 401


def test_confirm_activates_mfa_and_grants_permissions(client):
    u = new_owner()
    tok = login(client, u).json()["enrollment_token"]
    s = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()["secret"]
    with session_scope() as db:  # ainda pendente: nada ativo antes da confirmação
        user = db.get(User, uuid.UUID(u["user_id"]))
        assert user.mfa_enabled is False and user.totp_secret is None and pii.unseal(user.totp_pending_secret) == s
    assert (
        client.post("/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": "000000"}).status_code == 401
    )
    ok = client.post("/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": pyotp.TOTP(s).now()})
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).json()
    assert me["mfa"] is True and "emergency:stop" in me["permissions"]
    nxt = login(client, u).json()
    assert nxt["mfa_enrollment_required"] is False and "challenge_token" in nxt


def test_password_alone_cannot_replace_existing_factor(client):
    u = new_owner()
    tok, _, _ = enroll(client, u)
    r = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok})
    assert r.status_code == 409 and "já cadastrado" in r.json()["detail"]


def test_code_replay_rejected_next_step_accepted(client):
    u = new_owner()
    _, secret, _ = enroll(client, u)
    used = pyotp.TOTP(secret).now()
    ch = login(client, u).json()["challenge_token"]
    r = client.post("/v1/auth/mfa/verify", json={"challenge_token": ch, "code": used})
    assert r.status_code == 401 and "já utilizado" in r.json()["detail"]
    nxt = code_at(secret, 30)
    if nxt != used:
        assert client.post("/v1/auth/mfa/verify", json={"challenge_token": ch, "code": nxt}).status_code == 200


def test_mfa_brute_force_locks_and_password_does_not_reset_counter(client):
    u = new_owner()
    _, secret, _ = enroll(client, u)
    wrong = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"
    ch = login(client, u).json()["challenge_token"]
    for _ in range(4):
        assert client.post("/v1/auth/mfa/verify", json={"challenge_token": ch, "code": wrong}).status_code == 401
    ch = login(client, u).json()["challenge_token"]  # senha correta não zera falhas de MFA
    assert client.post("/v1/auth/mfa/verify", json={"challenge_token": ch, "code": wrong}).status_code == 401
    r = client.post("/v1/auth/mfa/verify", json={"challenge_token": ch, "code": code_at(secret, 30)})
    assert r.status_code == 423
    assert login(client, u).status_code == 401  # conta bloqueada também no passo da senha


def test_enrollment_failures_also_lock(client):
    u = new_owner()
    tok = login(client, u).json()["enrollment_token"]
    client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok})
    codes = [
        client.post("/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": "000001"}).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 423


def test_operator_without_mfa_role_logs_in_directly(client):
    u = manage._create_user(f"o-{uuid.uuid4().hex[:8]}@test.local", "Op", ["operator"], None)
    body = login(client, u).json()
    assert body["mfa_required"] is False and body["access_token"]


def test_reset_forces_new_enrollment_and_old_secret_dies(client):
    u = new_owner()
    _, secret, _ = enroll(client, u)
    with session_scope() as db:
        mfa.reset(db, db.get(User, uuid.UUID(u["user_id"])), actor="test")
    body = login(client, u).json()
    assert body["mfa_enrollment_required"] is True
    s2 = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": body["enrollment_token"]}).json()["secret"]
    assert s2 != secret
