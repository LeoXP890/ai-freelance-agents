"""Auditoria A02 (revalidação final): a reserva antes da chamada cobre o PIOR caso real — entrada pelo limite
superior em bytes (incluindo o schema, que também é enviado), saída máxima e raciocínio do Gemini — e o orçamento
da tarefa não recomeça do zero quando ela é retomada depois de uma queda."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from aicommerce.ai import llm
from aicommerce.db import session_scope
from aicommerce.models import AISpendHold, AIUsage
from tests.test_ai_budget import SCHEMA, go


class Extreme:
    """Provedor que cobra o MÁXIMO possível: entrada = limite superior e saída = tudo que foi permitido (+ raciocínio).
    Durante a chamada, lê a reserva gravada para comparar com o custo real."""

    name, simulated = "extremo", False

    def __init__(self):
        self.hold_seen: int | None = None

    def complete(self, req):
        with session_scope() as db:
            self.hold_seen = max(h.amount_micro_usd for h in db.execute(select(AISpendHold)).scalars())
        return llm.LLMResult(
            output={"a": "ok"},
            model=req.model,
            input_tokens=llm.input_tokens_upper(req.system, req.user, req.schema),
            cached_tokens=0,
            output_tokens=req.max_tokens + llm.thinking_budget(req.model, req.effort),
            stop_reason="end_turn",
            provider=self.name,
        )


@pytest.fixture
def provider():
    p = Extreme()
    llm.set_provider(p)
    yield p
    llm.set_provider(None)


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "gemini-2.5-flash", "gemini-2.5-pro"])
def test_hold_covers_worst_case_cost_even_with_multibyte_text_schema_and_thinking(provider, monkeypatch, model):
    monkeypatch.setattr(llm, "route", lambda **k: llm.Route("small", model, "teste"))
    user = "ação não-trivial: “preço” 💡 " * 200 + uuid.uuid4().hex  # muitos bytes por caractere
    _, _, cost = go(llm.RunBudget(max_tokens=200_000, max_calls=5, max_cost_micro_usd=10_000_000), user=user)
    assert provider.hold_seen is not None and cost <= provider.hold_seen  # nunca ultrapassa o que foi reservado


def test_old_estimate_would_have_underreserved_multibyte_and_schema():
    user = "ação “preço” 💡 " * 500
    old = llm._estimate_tokens("sistema") + llm._estimate_tokens(user)  # len//4, sem o schema
    assert llm.input_tokens_upper("sistema", user, SCHEMA) > 2 * old


def test_gemini_request_fixes_thinking_budget_that_the_reservation_counts():
    body = llm.GoogleLLMProvider(api_key="k")._body(
        llm.LLMRequest(
            model="gemini-2.5-pro", system="s", user="u", schema=SCHEMA, mock_output=None, max_tokens=1000, effort="low"
        ),
        True,
    )
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 128}
    assert llm.thinking_budget("gemini-2.5-flash", "low") == 0 and llm.thinking_budget("claude-opus-5-5", "medium") == 0


def test_resumed_task_keeps_what_it_already_spent(provider, monkeypatch):
    monkeypatch.setattr(llm, "route", lambda **k: llm.Route("small", "claude-haiku-5-5", "teste"))
    task_id = uuid.uuid4()
    limits = {"max_tool_calls": 1, "max_tokens_per_run": 200_000, "max_cost_micro_usd_per_run": 10_000_000}
    go(llm.RunBudget.for_task(limits, task_id), task_id=task_id)
    # processo "cai" e a tarefa é retomada: orçamento novo, mas carregado do ledger da própria tarefa
    resumed = llm.RunBudget.for_task(limits, task_id)
    assert resumed.calls == 1 and resumed.tokens > 0 and resumed.cost_micro_usd > 0
    provider.hold_seen = None
    with pytest.raises(llm.LimitReached):
        go(resumed, task_id=task_id)
    assert provider.hold_seen is None  # parou antes de chamar o provedor
    with session_scope() as db:
        paid = db.execute(select(AIUsage).where(AIUsage.task_id == task_id, AIUsage.request_cost_micro_usd > 0)).all()
    assert len(paid) == 1
