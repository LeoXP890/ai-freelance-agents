import pyotp

from aicommerce.identity.security import issue_access_token


def test_viewer_login_without_mfa_and_permissions(client, make_user):
    u = make_user("viewer")
    r = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]})
    assert r.status_code == 200 and r.json()["mfa_required"] is False
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["roles"] == ["viewer"] and "office:read" in me["permissions"]
    assert "tasks:create" not in me["permissions"]


def test_admin_roles_require_second_factor(client, make_user):
    u = make_user("owner")
    r = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]})
    body = r.json()
    assert body["mfa_required"] is True and "access_token" not in body
    bad = client.post("/v1/auth/mfa/verify", json={"challenge_token": body["challenge_token"], "code": "000000"})
    assert bad.status_code == 401
    ok = client.post(
        "/v1/auth/mfa/verify", json={"challenge_token": body["challenge_token"], "code": pyotp.TOTP(u["totp"]).now()}
    )
    assert ok.status_code == 200
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).json()
    assert me["mfa"] is True and "emergency:stop" in me["permissions"]


def test_challenge_token_is_not_an_access_token(client, make_user):
    u = make_user("owner")
    challenge = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()
    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {challenge['challenge_token']}"})
    assert r.status_code == 401


def test_owner_session_without_mfa_gets_no_permissions(client, make_user):
    u = make_user("owner")
    token = issue_access_token(u["id"], mfa=False)  # token válido, mas sem segundo fator
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/auth/me", headers=h).json()["permissions"] == []
    assert client.post("/v1/emergency-stop", json={"reason": "bypass"}, headers=h).status_code == 403


def test_rbac_denies_and_requires_auth(client, auth):
    viewer = auth("viewer")
    assert (
        client.post("/v1/agent-tasks", json={"agent_id": "luna", "kind": "research.mock"}, headers=viewer).status_code
        == 403
    )
    assert client.get("/v1/agent-presence/snapshot").status_code == 401
    assert client.get("/v1/agent-presence/snapshot", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.post("/v1/emergency-stop", json={"reason": "teste"}, headers=auth("operator")).status_code == 403


def test_wrong_password_and_lockout_are_persisted_and_audited(client, make_user, db):
    from sqlalchemy import select

    from aicommerce.models import AuditEvent

    u = make_user("viewer")
    for _ in range(5):
        assert client.post("/v1/auth/login", json={"email": u["email"], "password": "errada"}).status_code == 401
    # bloqueada mesmo com a senha correta
    r = client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]})
    assert r.status_code == 401 and "bloqueada" in r.json()["detail"]
    fails = (
        db.execute(
            select(AuditEvent).where(AuditEvent.subject_id == str(u["id"]), AuditEvent.action == "auth.login.failed")
        )
        .scalars()
        .all()
    )
    assert len(fails) == 5


def test_unknown_email_is_indistinguishable(client):
    r = client.post("/v1/auth/login", json={"email": "ninguem@test.local", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"] == "credenciais inválidas"


def test_request_id_and_mode_headers(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc12345-req"})
    assert r.headers["X-Request-ID"] == "abc12345-req" and r.headers["X-AIC-Mode"] == "DRY_RUN"
    assert client.get("/v1/system/info").json()["simulated"] is True
    assert client.get("/readyz").json()["database"] is True
