"""QA-002: falha de banco — API responde 503 explícito, readyz acusa, worker sobrevive e se recupera."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from aicommerce import worker
from aicommerce.db import get_db


def _boom(*_a, **_k):
    raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))


def test_api_returns_503_with_retry_after_when_db_down(client, auth):
    h = auth("operator")
    from aicommerce.app import app

    def broken_db():
        _boom()
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    try:
        r = client.get("/v1/auth/me", headers=h)
        assert r.status_code == 503 and r.headers["retry-after"] == "5"
        assert r.json()["detail"]["code"] == "DB_UNAVAILABLE"
        assert "refused" not in r.text and "psycopg" not in r.text  # sem detalhes de conexão
        assert client.get("/healthz").status_code == 200  # liveness não depende do banco
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert client.get("/v1/auth/me", headers=h).status_code == 200  # volta sozinho


def test_readyz_reports_database_down(client, monkeypatch):
    import aicommerce.app as app_mod

    dead = create_engine("postgresql+psycopg://x:y@127.0.0.1:1/nada", connect_args={"connect_timeout": 1})
    monkeypatch.setattr(app_mod, "get_engine", lambda: dead)
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable", "database": False}


def test_worker_cycle_survives_db_outage_with_backoff_then_recovers(monkeypatch, caplog):
    monkeypatch.setattr(worker, "beat", _boom)
    worked, failures = worker.cycle(0)
    assert (worked, failures) == (False, 1)
    _, failures = worker.cycle(failures)
    _, failures = worker.cycle(failures)
    assert failures == 3
    assert [worker.backoff_s(n) for n in (1, 2, 3, 8, 20)] == [0.5, 1.0, 2.0, 30.0, 30.0]
    assert "OperationalError" in caplog.text
    monkeypatch.undo()  # banco volta
    with caplog.at_level("INFO", logger="aicommerce.worker"):
        _, failures = worker.cycle(failures)
    assert failures == 0 and "recuperado" in caplog.text


@pytest.mark.parametrize("step", ["execute_next", "reconcile_next"])
def test_failure_mid_cycle_does_not_kill_worker(monkeypatch, step):
    from aicommerce.governance import executor

    monkeypatch.setattr(executor, step, _boom)
    assert worker.cycle(0) == (False, 1)


def test_metrics_scrape_survives_db_outage(client, monkeypatch):
    from aicommerce import db as db_mod
    from aicommerce.config import get_settings

    h = {"Authorization": f"Bearer {get_settings().metrics_token}"}
    ok = client.get("/metrics", headers=h)
    assert ok.status_code == 200 and "aic_db_up 1.0" in ok.text and "aic_worker_heartbeat_age_seconds" in ok.text

    def broken():
        _boom()
        yield  # pragma: no cover

    monkeypatch.setattr(db_mod, "session_scope", lambda: __import__("contextlib").contextmanager(broken)())
    down = client.get("/metrics", headers=h)
    assert down.status_code == 200  # scrape segue vivo: o Prometheus não confunde com API fora
    assert "aic_db_up 0.0" in down.text and "aic_http_requests_total" in down.text
    assert "aic_worker_heartbeat_age_seconds" not in down.text
