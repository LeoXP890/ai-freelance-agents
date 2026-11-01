"""Auditoria A02/A03: tetos de IA verificados ANTES da chamada (por execução e mensal, com reserva atômica) e cache
que nunca serve a resposta de um provedor/ambiente para outro."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import func, select

from aicommerce.ai import llm
from aicommerce.db import session_scope
from aicommerce.models import AISpendHold, AIUsage

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


class Spy:
    def __init__(self, name="spy", simulated=True, delay=0.0, fail=False):
        self.name, self.simulated, self.delay, self.fail = name, simulated, delay, fail
        self.requests = []

    def complete(self, req):
        self.requests.append(req)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise llm.LLMTransient("caiu")
        return llm.LLMResult(
            output={"a": self.name},
            model=req.model,
            input_tokens=50,
            cached_tokens=0,
            output_tokens=40,
            stop_reason="end_turn",
            provider=self.name,
        )


@pytest.fixture
def use():
    def _set(p):
        llm.set_provider(p)
        return p

    yield _set
    llm.set_provider(None)


def go(budget, user=None, **kw):
    return llm.call(
        agent_id="luna",
        purpose="teste",
        spec_tier="small",
        risk="LOW",
        attempt=1,
        system="sistema",
        user=user or f"pergunta {uuid.uuid4().hex}",
        schema=SCHEMA,
        mock_output={"a": "mock"},
        budget=budget,
        **kw,
    )


def holds() -> int:
    with session_scope() as db:
        return db.execute(select(func.count()).select_from(AISpendHold)).scalar_one()


def test_tiny_run_budget_stops_before_calling_the_provider(use):
    spy = use(Spy())
    with pytest.raises(llm.LimitReached):
        go(llm.RunBudget(max_tokens=1, max_calls=5, max_cost_micro_usd=1))
    assert spy.requests == [] and holds() == 0  # antes: aceitava 11 tokens / 4 µUS$ com teto 1/1


def test_output_capped_to_remaining_run_budget(use):
    spy = use(Spy())
    b = llm.RunBudget(max_tokens=1000, max_calls=5, max_cost_micro_usd=10_000_000)
    go(b, max_tokens=4000)
    assert spy.requests[0].max_tokens <= 1000 - llm._estimate_tokens("sistema")
    cost_cap = llm.RunBudget(max_tokens=10**6, max_calls=5, max_cost_micro_usd=400)  # haiku: 0,5 µUS$/token de saída
    go(cost_cap, max_tokens=4000)
    assert spy.requests[1].max_tokens <= 800


def test_monthly_budget_is_reserved_atomically(use, monkeypatch):
    use(Spy(delay=0.5))
    with session_scope() as db:
        spent = llm.month_spend_micro_usd(db)
    worst = llm.cost_micro_usd("claude-haiku-5-5", 200, 0, 4000)
    # sobra no mês exatamente o pior custo de UMA chamada: duas concorrentes, só uma pode passar
    monkeypatch.setattr(llm, "budget_remaining", lambda db: worst + 50)
    results, errors = [], []

    def worker():
        try:
            results.append(go(llm.RunBudget.from_limits({}), user="x" * 400))
        except llm.LimitReached as e:
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(results) == 1 and len(errors) == 1 and holds() == 0
    with session_scope() as db:
        assert llm.month_spend_micro_usd(db) > spent


def test_hold_released_when_provider_fails(use):
    use(Spy(fail=True))
    with pytest.raises(llm.LLMTransient):
        go(llm.RunBudget.from_limits({}))
    assert holds() == 0


def test_expired_hold_stops_blocking_and_becomes_cost(use):
    """Reserva vencida = processo caiu durante a chamada: deixa de ser reserva (não bloqueia para sempre) e vira custo
    lançado pelo valor máximo reservado (o teto mensal nunca subconta — A02/A07)."""
    import datetime as dt

    task_id = uuid.uuid4()
    with session_scope() as db:
        db.add(
            AISpendHold(
                agent_id="luna",
                amount_micro_usd=5_000,
                expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
                task_id=task_id,
                purpose="teste",
                provider="spy",
            )
        )
    use(Spy())
    go(llm.RunBudget.from_limits({}))  # a chamada seguinte acerta a reserva vencida e segue
    with session_scope() as db:
        settled = db.execute(select(AIUsage).where(AIUsage.task_id == task_id)).scalars().all()
        left = db.execute(select(AISpendHold).where(AISpendHold.task_id == task_id)).scalars().all()
    assert left == [] and len(settled) == 1
    assert settled[0].stop_reason == "HOLD_EXPIRED" and settled[0].request_cost_micro_usd == 5_000


def test_cache_never_crosses_providers(use):
    import datetime as dt

    q = f"pergunta cacheável {uuid.uuid4().hex}"
    use(llm.MockLLMProvider())
    first, _, _ = go(llm.RunBudget.from_limits({}), user=q, cache_ttl=dt.timedelta(hours=1))
    assert first.output == {"a": "mock"}
    real = use(Spy(name="real", simulated=False))
    second, _, _ = go(llm.RunBudget.from_limits({}), user=q, cache_ttl=dt.timedelta(hours=1))
    assert real.requests and second.output == {"a": "real"} and not second.cache_hit  # antes: devolvia a fixture mock
    third, _, _ = go(llm.RunBudget.from_limits({}), user=q, cache_ttl=dt.timedelta(hours=1))
    assert third.cache_hit and third.output == {"a": "real"} and len(real.requests) == 1
    with session_scope() as db:
        last = db.execute(select(AIUsage).order_by(AIUsage.id.desc()).limit(1)).scalar_one()
        assert last.cache_hit and last.simulated is False and last.provider == "real"


def test_output_outside_schema_is_rejected_and_never_cached(use):
    """Auditoria A06: inteiro exigido, texto recebido → erro tipado; nada no cache; tarefa pede correção."""
    import datetime as dt

    class Wrong(Spy):
        def complete(self, req):
            self.requests.append(req)
            return llm.LLMResult(
                output={"n": "dez"},
                model=req.model,
                input_tokens=5,
                cached_tokens=0,
                output_tokens=5,
                stop_reason="end_turn",
                provider="wrong",
            )

    use(Wrong())
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "additionalProperties": False,
    }
    q = f"quantos? {uuid.uuid4().hex}"
    for _ in range(2):
        with pytest.raises(llm.LLMOutputInvalid) as e:
            llm.call(
                agent_id="luna",
                purpose="t",
                spec_tier="small",
                risk="LOW",
                attempt=1,
                system="s",
                user=q,
                schema=schema,
                mock_output=None,
                budget=llm.RunBudget.from_limits({}),
                cache_ttl=dt.timedelta(hours=1),
            )
        assert "n" in str(e.value) and e.value.kind == "OUTPUT_INVALID"
    assert holds() == 0
