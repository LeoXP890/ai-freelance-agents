"""Robô de publicação: só proposta aprovada, só em LIVE, confere o anúncio e registra; para quando precisa de você."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, update

from aicommerce import robot
from aicommerce.ai.tools import ml
from aicommerce.config import RuntimeMode, get_settings
from aicommerce.db import session_scope
from aicommerce.models import ApprovalProposal, Opportunity, RobotRun
from tests.test_approvals import decide
from tests.test_platform_publish import clear_tool_cache, fake_ml, opportunity  # noqa: F401


class FakeDriver:
    def __init__(self, outcome):
        self.outcome, self.jobs = outcome, []

    def publish(self, job, *, rehearse):
        self.jobs.append(job)
        return self.outcome


@pytest.fixture
def approved(client, auth, monkeypatch, fake_ml):  # noqa: F811
    op, owner = auth("operator"), auth("owner")
    with session_scope() as db:  # outras propostas aprovadas de outros testes não entram nesta fila
        db.execute(
            update(ApprovalProposal)
            .where(ApprovalProposal.action_type == "PUBLISH_VIA_PLATFORM", ApprovalProposal.status == "AUTHORIZED")
            .values(status="EXPIRED")
        )
    oid = opportunity()
    prop = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=op).json()
    assert decide(client, owner, prop).status_code == 200
    base_get = ml._get

    def get(path):
        if path.startswith("/categories/MLB193527"):
            return {"path_from_root": [{"name": "Casa, Móveis e Decoração"}, {"name": "Cozinha"}]}
        if path == "/users/me":
            return {"id": 1}
        if "/items/search" in path:
            return {"results": ["MLB777"]}
        if path == "/items/MLB777":
            return {
                "id": "MLB777",
                "title": "Kit 3 Formas de Bolo",
                "price": 109.9,
                "status": "active",
                "permalink": "https://produto.mercadolivre.com.br/MLB-777",
            }
        return base_get(path)

    monkeypatch.setattr(ml, "_get", get)
    monkeypatch.setattr(ml, "_seller", None)
    monkeypatch.setattr(get_settings(), "mode", RuntimeMode.LIVE)
    yield uuid.UUID(prop["id"]), uuid.UUID(oid)
    with session_scope() as db:
        db.execute(delete(RobotRun))


def test_does_nothing_in_dry_run(approved, monkeypatch):
    monkeypatch.setattr(get_settings(), "mode", RuntimeMode.DRY_RUN)
    d = FakeDriver(robot.Outcome("DONE", "ok", "x"))
    assert robot.run_once(d) is None and d.jobs == []


def test_publishes_exactly_what_was_approved_and_records_listing(approved):
    pid, oid = approved
    d = FakeDriver(
        robot.Outcome("DONE", "Produto salvo", "salvar e exportar", "https://app.dropi.com.br/editar/produto/X")
    )
    assert robot.run_once(d) == "DONE"
    job = d.jobs[0]
    assert job.title == "Kit 3 Formas de Bolo" and job.price_minor == 10990
    assert job.category_path == ["Casa, Móveis e Decoração", "Cozinha"]
    with session_scope() as db:
        run = db.get(RobotRun, pid)
        assert run.state == "DONE" and run.listing_id == "MLB777"
        assert db.get(ApprovalProposal, pid).status == "SUCCEEDED"
        assert db.get(Opportunity, oid).status == "PUBLISHED"
    assert robot.run_once(d) is None  # não publica duas vezes


def test_price_divergence_on_marketplace_stops_for_owner(approved, monkeypatch):
    pid, _ = approved
    base = ml._get
    monkeypatch.setattr(
        ml, "_get", lambda path: {**base(path), "price": 83.46} if path == "/items/MLB777" else base(path)
    )
    assert robot.run_once(FakeDriver(robot.Outcome("DONE", "ok", "x"))) == "NEEDS_OWNER"
    with session_scope() as db:
        assert "diferente do aprovado" in db.get(RobotRun, pid).message
        assert db.get(ApprovalProposal, pid).status == "AUTHORIZED"  # nada registrado como publicado


def test_needs_owner_is_not_retried_but_failure_is_retried_once(approved):
    pid, _ = approved
    assert robot.run_once(FakeDriver(robot.Outcome("FAILED", "tempo esgotado", "importar"))) == "FAILED"
    assert robot.run_once(FakeDriver(robot.Outcome("NEEDS_OWNER", "termos do fornecedor", "importar"))) == "NEEDS_OWNER"
    assert robot.run_once(FakeDriver(robot.Outcome("DONE", "ok", "x"))) is None
    with session_scope() as db:
        assert db.get(RobotRun, pid).attempts == 2


def test_expired_or_emergency_stop_blocks(approved, monkeypatch):
    from aicommerce.governance import killswitch

    monkeypatch.setattr(killswitch, "is_active", lambda db: True)
    assert robot.run_once(FakeDriver(robot.Outcome("DONE", "ok", "x"))) is None
    monkeypatch.setattr(killswitch, "is_active", lambda db: False)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    assert robot.run_once(FakeDriver(robot.Outcome("DONE", "ok", "x")), now=later) is None


def test_robot_stop_shows_as_alert(approved):
    from aicommerce.commerce import alerts

    robot.run_once(FakeDriver(robot.Outcome("NEEDS_OWNER", "o fornecedor pede aceite de termos", "importar")))
    with session_scope() as db:
        a = [x for x in alerts.compute(db) if x["kind"] == "ROBOT_NEEDS_OWNER"]
    assert a and a[0]["severity"] == "CRITICAL" and "aceite de termos" in a[0]["message"]


def test_old_proposal_without_verified_economics_is_never_published(approved):
    pid, _ = approved
    with session_scope() as db:
        prop = db.get(ApprovalProposal, pid)
        prop.payload = {k: v for k, v in prop.payload.items() if k not in ("economics", "market_check")}
    d = FakeDriver(robot.Outcome("DONE", "ok", "x"))
    assert robot.run_once(d) == "NEEDS_OWNER" and d.jobs == []
    with session_scope() as db:
        assert "não publica" in db.get(RobotRun, pid).message


def test_alert_disappears_after_owner_revokes(approved):
    from aicommerce.commerce import alerts

    pid, _ = approved
    robot.run_once(FakeDriver(robot.Outcome("NEEDS_OWNER", "termos do fornecedor", "importar")))
    with session_scope() as db:
        db.get(ApprovalProposal, pid).status = "REVOKED"
    with session_scope() as db:
        assert not [x for x in alerts.compute(db) if x["kind"].startswith("ROBOT_")]
