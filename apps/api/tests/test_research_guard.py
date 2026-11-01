"""Pesquisa externa dos agentes com teto, intervalo, trilha e inventário honesto (issue #3).

Nenhuma chamada real sai nos testes (rede bloqueada pelo conftest); os provedores são simulados por transporte HTTP
falso. Brave é pago: sem teto mensal configurado, nenhuma consulta é feita."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from aicommerce.ai import research_guard
from aicommerce.ai.research_guard import ResearchBlocked, guarded
from aicommerce.ai.specs import SPECS
from aicommerce.ai.web_research import BraveWebResearch, WebResearchUnavailable
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.models import ResearchCall

KEY = "chave-brave-de-teste-nunca-gravada"


def brave_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"web": {"results": [{"url": "https://exemplo.com.br/a", "title": "A", "description": "x"}]}}
    )


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    with session_scope() as db:
        db.execute(delete(ResearchCall))
    monkeypatch.setattr(get_settings(), "research_min_interval_s", 0.0)
    yield
    with session_scope() as db:
        db.execute(delete(ResearchCall))


def calls(provider):
    with session_scope() as db:
        return [
            (c.status, c.http_status, c.detail, c.query_sha256)
            for c in db.execute(
                select(ResearchCall).where(ResearchCall.provider == provider).order_by(ResearchCall.id)
            ).scalars()
        ]


def brave(handler):
    return lambda: BraveWebResearch(KEY, transport=httpx.MockTransport(handler)).search("garrafa térmica", 3)


def test_paid_search_without_monthly_cap_makes_no_call(monkeypatch):
    hits = []
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 0)
    with pytest.raises(ResearchBlocked, match="sem teto"):
        guarded("brave_search", "t", "garrafa", brave(lambda r: hits.append(r) or brave_ok(r)))
    assert hits == [] and calls("brave_search")[0][0] == "BLOCKED"


def test_cap_and_minimum_interval(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 2)
    assert guarded("brave_search", "t", "a", brave(brave_ok))
    monkeypatch.setattr(get_settings(), "research_min_interval_s", 3600.0)
    with pytest.raises(ResearchBlocked, match="intervalo"):
        guarded("brave_search", "t", "b", brave(brave_ok))
    monkeypatch.setattr(get_settings(), "research_min_interval_s", 0.0)
    assert guarded("brave_search", "t", "c", brave(brave_ok))
    with pytest.raises(ResearchBlocked, match="teto mensal de 2"):
        guarded("brave_search", "t", "d", brave(brave_ok))
    assert [c[0] for c in calls("brave_search")] == ["OK", "BLOCKED", "OK", "BLOCKED"]


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_provider_errors_are_recorded_with_http_status_and_no_data(monkeypatch, code):
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 10)
    with pytest.raises(WebResearchUnavailable, match=f"HTTP {code}"):
        guarded("brave_search", "t", "garrafa", brave(lambda r: httpx.Response(code, json={"error": "x"})))
    status, http, detail, _ = calls("brave_search")[-1]
    assert (status, http) == ("ERROR", code) and KEY not in detail


def test_errors_count_toward_cap_so_retries_cannot_run_up_the_bill(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 2)
    for _ in range(2):
        with pytest.raises(WebResearchUnavailable):
            guarded("brave_search", "t", "x", brave(lambda r: httpx.Response(429)))
    with pytest.raises(ResearchBlocked):
        guarded("brave_search", "t", "x", brave(brave_ok))


def test_trail_never_stores_key_or_raw_query(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 5)
    guarded("brave_search", "t", "Fornecedor Secreto LTDA", brave(brave_ok))
    with session_scope() as db:
        row = db.execute(select(ResearchCall)).scalar_one()
        blob = " ".join(str(getattr(row, c.name)) for c in ResearchCall.__table__.columns)
    assert KEY not in blob and "Fornecedor Secreto" not in blob and len(row.query_sha256) == 64


def test_luna_uses_the_guard_and_reports_unavailability_without_inventing(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_web_source", "brave")
    monkeypatch.setattr(get_settings(), "research_brave_api_key", SecretStr(KEY))
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 0)  # sem orçamento: não pesquisa
    with session_scope() as db:
        ctx = SPECS["research.opportunities"].build(db, {"topic": "garrafa térmica"})
    assert ctx.facts["fontes_web_nao_verificadas"] == 0 and "teto" in ctx.facts["web_pesquisa_indisponivel"]
    assert not any(s["id"].startswith("W") for s in ctx.sources)
    assert calls("brave_search")[0][0] == "BLOCKED"


def test_inventory_states_are_honest(monkeypatch, client, auth):
    inv = {p["provider"]: p for p in research_guard.inventory()}
    assert inv["mercadolivre_trends"]["status"] == "mock"
    assert inv["brave_search"]["status"] == "unavailable" and inv["brave_search"]["paid"] is True
    assert inv["melhor_envio_sandbox"]["status"] == "unavailable"
    monkeypatch.setattr(get_settings(), "research_web_source", "brave")
    monkeypatch.setattr(get_settings(), "research_brave_api_key", SecretStr(KEY))
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 3)
    assert {p["provider"]: p for p in research_guard.inventory()}["brave_search"]["status"] == "configured"
    guarded("brave_search", "t", "a", brave(brave_ok))
    b = {p["provider"]: p for p in research_guard.inventory()}["brave_search"]
    assert b["status"] == "verified" and b["used_in_period"] == 1 and b["cap"] == 3
    r = client.get("/v1/research/sources", headers=auth("viewer"))
    assert r.status_code == 200 and KEY not in r.text
    body = r.json()
    assert {p["provider"] for p in body["providers"]} == set(research_guard.PROVIDERS) and "luna" in body["by_agent"]


def test_concurrent_workers_cannot_spend_the_same_last_slot(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 1)
    barrier = Barrier(6)
    hits = []

    def attempt(i):
        barrier.wait(timeout=10)
        try:
            guarded("brave_search", "concurrent", str(i), lambda: hits.append(i))
            return "OK"
        except ResearchBlocked:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, range(6)))
    assert len(hits) == outcomes.count("OK") == 1
    assert outcomes.count("BLOCKED") == 5


def test_interrupted_reservation_is_not_automatically_refunded(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 1)

    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        guarded("brave_search", "interrupted", "private", interrupted)
    assert calls("brave_search")[0][0] == "IN_FLIGHT"
    with pytest.raises(ResearchBlocked):
        guarded("brave_search", "retry", "private", lambda: pytest.fail("must not call"))


def test_upstream_exception_cannot_leak_secret_into_audit(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_brave_monthly_cap", 1)

    def bad():
        raise RuntimeError("HTTP 503 secret=" + KEY)

    with pytest.raises(RuntimeError):
        guarded("brave_search", "error", "private", bad)
    assert calls("brave_search")[0][2] == "RuntimeError"
