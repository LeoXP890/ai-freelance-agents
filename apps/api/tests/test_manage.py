import io
import uuid

import pytest

from aicommerce import manage


def email():
    return f"m-{uuid.uuid4().hex[:8]}@test.local"


def test_create_owner_prints_password_once_and_defers_mfa(client, capsys):
    e = email()
    assert manage.main(["create-user", "--email", e, "--name", "Dono", "--role", "owner"]) == 0
    out = capsys.readouterr().out
    assert "Senha (exibida uma única vez)" in out and "QR code no primeiro login" in out
    assert "TOTP" not in out and "otpauth" not in out  # segredo de MFA nunca sai pelo terminal
    password = out.split("Senha (exibida uma única vez): ")[1].split()[0]
    body = client.post("/v1/auth/login", json={"email": e, "password": password}).json()
    assert body["mfa_enrollment_required"] is True


def test_operator_password_from_stdin(client, monkeypatch, capsys):
    e = email()
    monkeypatch.setattr("sys.stdin", io.StringIO("uma-senha-bem-longa\n"))
    assert manage.main(["create-user", "--email", e, "--name", "Op", "--role", "operator", "--password-stdin"]) == 0
    assert "Senha" not in capsys.readouterr().out
    r = client.post("/v1/auth/login", json={"email": e, "password": "uma-senha-bem-longa"})
    assert r.json()["mfa_required"] is False


def test_rejects_duplicate_unknown_role_and_short_password():
    e = email()
    manage._create_user(e, "A", ["viewer"], None)
    with pytest.raises(manage.CommandError, match="já existe"):
        manage._create_user(e, "A", ["viewer"], None)
    with pytest.raises(manage.CommandError, match="papel desconhecido"):
        manage._create_user(email(), "A", ["superuser"], None)
    with pytest.raises(manage.CommandError, match="ao menos"):
        manage._create_user(email(), "A", ["viewer"], "curta")


def test_reset_mfa_unknown_user():
    assert manage.main(["reset-mfa", "--email", "ninguem@test.local"]) == 2


def test_set_password_resets_forgotten_password_and_kills_sessions(client, make_user):
    import io
    import sys

    from aicommerce import manage
    from tests.conftest import login

    u = make_user("operator")
    old_tok = login(client, u)
    dev = client.post("/v1/auth/devices", headers={"Authorization": f"Bearer {old_tok}"}, json={"name": "mac"}).json()
    stdin = sys.stdin
    try:
        sys.stdin = io.StringIO("senha-redefinida-pelo-dono\n")
        assert manage.main(["set-password", "--email", u["email"], "--password-stdin"]) == 0
        sys.stdin = io.StringIO("curta\n")
        assert manage.main(["set-password", "--email", u["email"], "--password-stdin"]) == 2
    finally:
        sys.stdin = stdin
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {old_tok}"}).status_code == 401
    assert client.post("/v1/auth/device-login", json={"device_token": dev["device_token"]}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 401
    ok = client.post("/v1/auth/login", json={"email": u["email"], "password": "senha-redefinida-pelo-dono"})
    assert ok.status_code == 200


def test_deactivate_user_kills_sessions_and_is_audited(client, auth):
    from sqlalchemy import select

    from aicommerce.db import session_scope
    from aicommerce.models import AuditEvent, User

    e = email()
    monkey = io.StringIO("uma-senha-bem-longa\n")
    import sys

    old, sys.stdin = sys.stdin, monkey
    try:
        assert manage.main(["create-user", "--email", e, "--name", "Op", "--role", "operator", "--password-stdin"]) == 0
    finally:
        sys.stdin = old
    tok = client.post("/v1/auth/login", json={"email": e, "password": "uma-senha-bem-longa"}).json()["access_token"]
    assert manage.main(["deactivate-user", "--email", e]) == 0
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": e, "password": "uma-senha-bem-longa"}).status_code in (401, 403)
    with session_scope() as db:
        uid = db.execute(select(User.id).where(User.email == e)).scalar_one()
        assert db.execute(
            select(AuditEvent).where(AuditEvent.subject_id == str(uid), AuditEvent.action == "user.deactivated")
        ).scalar_one()
    assert manage.main(["deactivate-user", "--email", "ninguem@test.local"]) == 2
