"""Testes contra PostgreSQL real (o mesmo motor de produção): locks, triggers e SKIP LOCKED são parte
do comportamento sob teste. Banco efêmero `aicommerce_test` recriado a cada sessão de testes.

Isolamento entre testes por dados (contas/usuários únicos), não por TRUNCATE — as tabelas append-only
recusam TRUNCATE por design.
"""

from __future__ import annotations

import os
import uuid

PG_HOST = os.environ.get("AIC_TEST_PG_HOST", "localhost")
PG_PORT = os.environ.get("AIC_TEST_PG_PORT", "55440")
PG_USER = os.environ.get("AIC_TEST_PG_USER", "aicommerce")
PG_PASS = os.environ.get("AIC_TEST_PG_PASSWORD", "aicommerce")
TEST_DB = "aicommerce_test"

os.environ.update(
    {
        "AIC_ENVIRONMENT": "test",
        "AIC_MODE": "DRY_RUN",
        "AIC_DATABASE_URL": f"postgresql+psycopg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{TEST_DB}",
        "AIC_JWT_SECRET": "test-secret-test-secret-test-secret-0123",
        "AIC_SEED_DEMO_USERS": "false",
        "AIC_OFFICE_POLL_INTERVAL_MS": "50",
    }
)

import psycopg  # noqa: E402
import pyotp  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def no_external_network():
    """Testes nunca saem da máquina (seção 4: nada de IA paga, marketplace ou pagamento reais por acidente).
    Só o próprio computador (banco, Redis, servidor de teste) é permitido; o resto falha na hora."""
    import ipaddress
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("localhost",) and not host.startswith("/"):
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
            if not local:
                raise ConnectionRefusedError(f"rede externa bloqueada nos testes: {address}")
        return real_connect(self, address)

    socket.socket.connect = guarded
    yield
    socket.socket.connect = real_connect


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname="postgres", autocommit=True
    ) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    command.upgrade(cfg, "head")
    from aicommerce.commerce import channels
    from aicommerce.db import session_scope
    from aicommerce.seed import seed_reference, seed_simulation

    with session_scope() as db:
        seed_reference(db)
        seed_simulation(db)
        for ch in channels.MARKETPLACES:  # contas simuladas conectadas (o executor exige token válido)
            channels.connect_simulated(db, ch, actor="test-setup")
    yield
    from aicommerce.db import get_engine

    get_engine().dispose()


@pytest.fixture
def client():
    from aicommerce.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    from aicommerce.db import session_scope

    with session_scope() as s:
        yield s


class UserFactory:
    PASSWORD = "senha-forte-de-teste-1"

    def __call__(self, *roles: str, mfa: bool | None = None):
        from aicommerce.db import session_scope
        from aicommerce.identity.security import new_totp_secret
        from aicommerce.identity.service import create_user

        needs_mfa = mfa if mfa is not None else any(r in ("owner", "admin", "finance") for r in roles)
        secret = new_totp_secret() if needs_mfa else None
        email = f"u-{uuid.uuid4().hex[:10]}@test.local"
        with session_scope() as s:
            user = create_user(
                s, email=email, display_name="Teste", password=self.PASSWORD, roles=list(roles), totp_secret=secret
            )
            return {"id": user.id, "email": email, "password": self.PASSWORD, "totp": secret}


@pytest.fixture
def make_user():
    return UserFactory()


def login(client: TestClient, user: dict) -> str:
    r = client.post("/v1/auth/login", json={"email": user["email"], "password": user["password"]})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["mfa_required"]:
        r = client.post(
            "/v1/auth/mfa/verify",
            json={"challenge_token": body["challenge_token"], "code": pyotp.TOTP(user["totp"]).now()},
        )
        assert r.status_code == 200, r.text
        body = r.json()
    return body["access_token"]


@pytest.fixture
def auth(client, make_user):
    """auth('owner') -> headers autenticados (com MFA quando o papel exige)."""

    def _auth(*roles: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {login(client, make_user(*roles))}"}

    return _auth


@pytest.fixture
def budget_account():
    """Conta simulada nova com teto/hold conhecidos (isolamento por dados)."""
    from aicommerce.db import session_scope
    from aicommerce.finance import ledger

    def _make(cap: int = 100_000, hold: int = 10_000):
        with session_scope() as s:
            acc = ledger.ensure_account(s, f"test-{uuid.uuid4().hex[:8]}", "Conta de teste (SIMULADO)")
            ledger.set_budget(s, acc.id, approved_cap_minor=cap, safety_hold_minor=hold, approved_by="test")
            return acc.id

    return _make
