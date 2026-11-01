from tests.conftest import login


def test_change_password_revokes_old_sessions(client, make_user):
    u = make_user("operator")
    old = {"Authorization": f"Bearer {login(client, u)}"}
    r = client.post(
        "/v1/auth/password",
        headers=old,
        json={"current_password": u["password"], "new_password": "nova-senha-bem-forte-42"},
    )
    assert r.status_code == 200
    new = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/v1/auth/me", headers=new).status_code == 200
    assert client.get("/v1/auth/me", headers=old).status_code == 401  # sessão antiga encerrada
    assert client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 401
    assert login(client, {**u, "password": "nova-senha-bem-forte-42"})


def test_change_password_validations(client, make_user):
    u = make_user("viewer")
    h = {"Authorization": f"Bearer {login(client, u)}"}
    for current, new, msg in (
        (u["password"] + "x", "outra-senha-forte-1", "incorreta"),
        (u["password"], "curta", "ao menos"),
        (u["password"], u["password"], "diferente"),
        (u["password"], u["email"].split("@")[0] + "-1234567", "e-mail"),
    ):
        r = client.post("/v1/auth/password", headers=h, json={"current_password": current, "new_password": new})
        assert r.status_code == 422 and msg in r.json()["detail"], (new, r.text)


def test_owner_keeps_mfa_on_new_token(client, make_user):
    u = make_user("owner")
    h = {"Authorization": f"Bearer {login(client, u)}"}
    r = client.post(
        "/v1/auth/password",
        headers=h,
        json={"current_password": u["password"], "new_password": "senha-nova-do-dono-99"},
    )
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["mfa"] is True and "approvals:decide" in me["permissions"]


def test_mfa_reset_revokes_sessions(client, make_user):
    import uuid

    from aicommerce.db import session_scope
    from aicommerce.identity import mfa
    from aicommerce.models import User

    u = make_user("owner")
    h = {"Authorization": f"Bearer {login(client, u)}"}
    with session_scope() as db:
        mfa.reset(db, db.get(User, uuid.UUID(str(u["id"]))), actor="test")
    assert client.get("/v1/auth/me", headers=h).status_code == 401
