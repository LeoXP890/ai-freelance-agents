"""INF-003: códigos de recuperação de MFA e gestão de usuários sem escalada de privilégio."""

import pyotp

from tests.conftest import login
from tests.test_mfa_enrollment import code_at, enroll, new_owner


# ───────────── códigos de recuperação ─────────────
def test_enrollment_returns_ten_single_use_recovery_codes(client):
    u = new_owner()
    tok = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()[
        "enrollment_token"
    ]
    start = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()
    r = client.post(
        "/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": pyotp.TOTP(start["secret"]).now()}
    ).json()
    codes = r["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10 and all(len(c) == 19 for c in codes)

    def recover(code):
        ch = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()
        return client.post(
            "/v1/auth/mfa/recover", json={"challenge_token": ch["challenge_token"], "recovery_code": code}
        )

    ok = recover(codes[0].lower().replace("-", " "))  # tolera minúsculas/espaços
    assert ok.status_code == 200 and ok.json()["recovery_codes_left"] == 9
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).json()
    assert me["mfa"] is True and "approvals:decide" in me["permissions"]
    assert recover(codes[0]).status_code == 401  # uso único


def test_recovery_codes_are_stored_hashed_and_wrong_codes_lock(client):
    from sqlalchemy import select

    from aicommerce.db import session_scope
    from aicommerce.models import MfaRecoveryCode

    u = new_owner()
    enroll(client, u)
    with session_scope() as s:
        stored = s.execute(select(MfaRecoveryCode.code_sha256)).scalars().all()
    assert all(len(h) == 64 for h in stored)
    for _ in range(5):
        ch = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()
        r = client.post(
            "/v1/auth/mfa/recover",
            json={"challenge_token": ch["challenge_token"], "recovery_code": "AAAA-BBBB-CCCC-DDDD"},
        )
    assert r.status_code in (401, 423)
    assert client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 401


def test_regenerate_requires_current_totp_and_invalidates_old(client):
    u = new_owner()
    tok = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()[
        "enrollment_token"
    ]
    start = client.post("/v1/auth/mfa/enroll/start", json={"enrollment_token": tok}).json()
    first = client.post(
        "/v1/auth/mfa/enroll/confirm", json={"enrollment_token": tok, "code": pyotp.TOTP(start["secret"]).now()}
    ).json()
    h = {"Authorization": f"Bearer {first['access_token']}"}
    assert client.post("/v1/auth/mfa/recovery-codes", headers=h, json={"code": "000000"}).status_code == 401
    new = client.post("/v1/auth/mfa/recovery-codes", headers=h, json={"code": code_at(start["secret"], 30)})
    assert new.status_code == 200, new.text
    assert client.get("/v1/auth/mfa/recovery-codes", headers=h).json()["remaining"] == 10
    ch = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()
    old = client.post(
        "/v1/auth/mfa/recover",
        json={"challenge_token": ch["challenge_token"], "recovery_code": first["recovery_codes"][1]},
    )
    assert old.status_code == 401


# ───────────── gestão de usuários ─────────────
def test_admin_creates_user_with_temporary_password(client, auth):
    admin = auth("admin")
    r = client.post(
        "/v1/users",
        headers=admin,
        json={"email": "Nova.Pessoa@Test.local", "display_name": "Nova Pessoa", "roles": ["viewer"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "nova.pessoa@test.local" and body["roles"] == ["viewer"]
    tok = client.post(
        "/v1/auth/login", json={"email": "nova.pessoa@test.local", "password": body["temporary_password"]}
    ).json()
    assert tok["mfa_required"] is False and "access_token" in tok
    assert (
        client.post(
            "/v1/users",
            headers=admin,
            json={"email": "nova.pessoa@test.local", "display_name": "Xis", "roles": ["viewer"]},
        ).status_code
        == 409
    )
    listed = client.get("/v1/users", headers=admin).json()
    assert "temporary_password" not in str(listed) and "password_hash" not in str(listed)


def test_no_privilege_escalation(client, auth, make_user):
    admin = auth("admin")
    # admin não tem approvals:decide etc. → não pode conceder owner nem criar owner
    assert (
        client.post(
            "/v1/users", headers=admin, json={"email": "x1@test.local", "display_name": "Xis", "roles": ["owner"]}
        ).status_code
        == 403
    )
    victim = make_user("viewer")
    assert client.put(f"/v1/users/{victim['id']}/roles", headers=admin, json={"roles": ["owner"]}).status_code == 403
    # dados pessoais de pedidos (orders:pii) só podem ser concedidos por quem já os acessa (LGPD)
    r = client.put(f"/v1/users/{victim['id']}/roles", headers=admin, json={"roles": ["operator"]})
    assert r.status_code == 403 and "orders:pii" in r.text
    assert client.post(f"/v1/users/{make_user('operator')['id']}/deactivate", headers=admin).status_code == 403
    owner_h = auth("owner")
    assert (
        client.put(f"/v1/users/{victim['id']}/roles", headers=owner_h, json={"roles": ["operator"]}).status_code == 200
    )
    # admin não pode mexer em quem tem mais permissões (owner)
    owner = make_user("owner")
    assert client.post(f"/v1/users/{owner['id']}/deactivate", headers=admin).status_code == 403
    # operador não gerencia usuários
    assert client.get("/v1/users", headers=auth("operator")).status_code == 403


def test_cannot_change_self_or_remove_last_owner(client, make_user):
    import pytest
    from fastapi import HTTPException
    from sqlalchemy import update

    from aicommerce.api.routes_users import _guard_last_owner
    from aicommerce.db import get_sessionmaker
    from aicommerce.models import User

    o = make_user("owner")
    h = {"Authorization": f"Bearer {login(client, o)}"}
    assert client.post(f"/v1/users/{o['id']}/deactivate", headers=h).status_code == 409
    assert client.put(f"/v1/users/{o['id']}/roles", headers=h, json={"roles": ["viewer"]}).status_code == 409

    other = make_user("owner")
    s = get_sessionmaker()()
    try:  # transação desfeita ao final: 'other' vira o único proprietário ativo
        s.execute(update(User).where(User.id != other["id"]).values(is_active=False))
        target = s.get(User, other["id"])
        with pytest.raises(HTTPException) as e:
            _guard_last_owner(s, target, losing_owner=True)
        assert e.value.status_code == 409
        s.execute(update(User).where(User.id == o["id"]).values(is_active=True))
        _guard_last_owner(s, target, losing_owner=True)  # com outro proprietário ativo, pode
    finally:
        s.rollback()
        s.close()


def test_role_change_and_deactivation_revoke_sessions(client, auth, make_user):
    admin = auth("owner")
    u = make_user("operator")
    old = {"Authorization": f"Bearer {login(client, u)}"}
    assert client.get("/v1/auth/me", headers=old).status_code == 200
    r = client.put(f"/v1/users/{u['id']}/roles", headers=admin, json={"roles": ["viewer"]})
    assert r.status_code == 200 and r.json()["roles"] == ["viewer"]
    assert client.get("/v1/auth/me", headers=old).status_code == 401
    fresh = {"Authorization": f"Bearer {login(client, u)}"}
    assert "tasks:create" not in client.get("/v1/auth/me", headers=fresh).json()["permissions"]
    assert client.post(f"/v1/users/{u['id']}/deactivate", headers=admin).json()["active"] is False
    assert client.get("/v1/auth/me", headers=fresh).status_code == 401
    assert client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 401
    assert client.post(f"/v1/users/{u['id']}/activate", headers=admin).json()["active"] is True
    assert client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 200


def test_manager_resets_mfa_and_unlocks(client, auth, make_user):
    admin = auth("owner")  # finance tem fiscal:read, que o admin técnico não tem
    f = make_user("finance")
    r = client.post(f"/v1/users/{f['id']}/reset-mfa", headers=admin)
    assert r.status_code == 200 and r.json()["mfa_enabled"] is False
    assert (
        client.post("/v1/auth/login", json={"email": f["email"], "password": f["password"]}).json()[
            "mfa_enrollment_required"
        ]
        is True
    )
    for _ in range(5):
        client.post("/v1/auth/login", json={"email": f["email"], "password": "errada-123456"})
    assert client.post("/v1/auth/login", json={"email": f["email"], "password": f["password"]}).status_code == 401
    assert client.post(f"/v1/users/{f['id']}/unlock", headers=admin).json()["locked"] is False
    assert client.post("/v1/auth/login", json={"email": f["email"], "password": f["password"]}).status_code == 200


def test_user_management_is_audited(client, auth, make_user):
    admin = auth("owner")  # finance tem fiscal:read, que o admin técnico não tem
    u = make_user("viewer")
    assert client.put(f"/v1/users/{u['id']}/roles", headers=admin, json={"roles": ["finance"]}).status_code == 200
    from sqlalchemy import select

    from aicommerce.db import session_scope
    from aicommerce.models import AuditEvent

    with session_scope() as s:
        row = s.execute(
            select(AuditEvent).where(AuditEvent.action == "user.roles.changed", AuditEvent.subject_id == str(u["id"]))
        ).scalar_one()
        assert row.payload == {"before": ["viewer"], "after": ["finance"]}
