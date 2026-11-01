"""Critério de aceite de custo da finalização: NENHUMA chamada é autorizada quando a reserva máxima admissível passa do
saldo — nem da execução (tokens/custo) nem do mês. Teste aleatório (semente fixa) com modelos diferentes, troca de
modelo na nova tentativa, textos com muitos bytes por caractere, raciocínio do Gemini e teto mensal apertado; o
provedor falso cobra SEMPRE o máximo que a requisição permite."""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import func, select

from aicommerce.ai import llm
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.models import AISpendHold
from tests.test_ai_budget import SCHEMA

MODELS = ["claude-haiku-5-5", "claude-opus-5-5", "gemini-2.5-flash", "gemini-2.5-pro", "modelo-desconhecido-x"]
ALPHABET = "abcdefghij ação preço ü ✓ 💡 京 “aspas” {json}"


class MaxCharge:
    """Cobra o máximo possível e registra o que foi autorizado (com o estado das reservas no momento da chamada)."""

    name, simulated = "max-charge", False

    def __init__(self):
        self.calls: list[dict] = []

    def complete(self, req):
        with session_scope() as db:
            held = db.execute(select(func.coalesce(func.sum(AISpendHold.amount_micro_usd), 0))).scalar_one()
        upper = llm.input_tokens_upper(req.system, req.user, req.schema)
        out = req.max_tokens + llm.thinking_budget(req.model, req.effort)
        self.calls.append({"req": req, "upper": upper, "out": out, "held": int(held)})
        return llm.LLMResult(
            output={"a": "ok"},
            model=req.model,
            input_tokens=upper,
            cached_tokens=0,
            output_tokens=out,
            stop_reason="end_turn",
            provider=self.name,
        )


def month_spend() -> int:
    with session_scope() as db:
        return llm.month_spend_micro_usd(db)


@pytest.fixture
def maxcharge(monkeypatch):
    p = MaxCharge()
    llm.set_provider(p)
    yield p
    llm.set_provider(None)


def test_randomized_calls_never_exceed_run_or_monthly_budget(maxcharge, monkeypatch):
    rnd = random.Random(20261025)
    cap_room = 400_000  # µUS$ disponíveis no "mês" do teste, além do que já foi gasto pelos outros testes
    monkeypatch.setattr(get_settings(), "ai_monthly_budget_micro_usd", month_spend() + cap_room)
    blocked_run = blocked_month = authorized = 0
    for _ in range(60):
        model = rnd.choice(MODELS)
        monkeypatch.setattr(llm, "route", lambda m=model, **k: llm.Route("small", m, "teste adversarial"))
        budget = llm.RunBudget(
            max_tokens=rnd.randint(300, 60_000), max_calls=rnd.randint(1, 3), max_cost_micro_usd=rnd.randint(50, 80_000)
        )
        budget.tokens, budget.cost_micro_usd = rnd.randint(0, 2_000), rnd.randint(0, 40)
        tokens_before, cost_before = budget.tokens, budget.cost_micro_usd
        user = "".join(rnd.choice(ALPHABET) for _ in range(rnd.randint(1, 9_000))) + uuid.uuid4().hex
        n = len(maxcharge.calls)
        spend_before = month_spend()
        try:
            _, _, cost = llm.call(
                agent_id="luna",
                purpose="adversarial",
                spec_tier="small",
                risk="LOW",
                attempt=rnd.choice([1, 2]),
                system="sistema",
                user=user,
                schema=SCHEMA,
                mock_output={"a": "x"},
                budget=budget,
                max_tokens=rnd.choice([256, 1_000, 4_000, 16_000]),
                effort=rnd.choice(["low", "medium"]),
            )
        except llm.LimitReached as e:
            assert len(maxcharge.calls) == n  # bloqueado ANTES de chamar o provedor
            if "mensal" in str(e):
                blocked_month += 1
            else:
                blocked_run += 1
            continue
        authorized += 1
        c = maxcharge.calls[-1]
        worst = llm.cost_micro_usd(model, c["upper"], 0, c["out"])
        assert cost == worst  # o provedor cobrou o máximo permitido…
        assert worst <= budget.max_cost_micro_usd - cost_before  # …e mesmo assim coube no saldo da execução
        assert c["upper"] + c["out"] <= budget.max_tokens - tokens_before  # e no saldo de tokens
        assert worst <= c["held"]  # a reserva mensal cobria o pior caso
        assert month_spend() - spend_before == cost
    assert authorized >= 5 and blocked_run >= 5 and blocked_month >= 1  # os três caminhos foram exercitados
    assert month_spend() <= get_settings().ai_monthly_budget_micro_usd  # o mês nunca passou do teto


def test_exact_boundary_fits_and_one_microdollar_less_blocks(maxcharge, monkeypatch):
    monkeypatch.setattr(llm, "route", lambda **k: llm.Route("small", "claude-haiku-5-5", "teste"))
    probe = llm.RunBudget(max_tokens=100_000, max_calls=5, max_cost_micro_usd=10_000_000)
    args = dict(
        agent_id="luna",
        purpose="fronteira",
        spec_tier="small",
        risk="LOW",
        attempt=1,
        system="s",
        user="pergunta fixa " + uuid.uuid4().hex,
        schema=SCHEMA,
        mock_output={"a": "x"},
        max_tokens=256,
    )
    _, _, worst = llm.call(budget=probe, **args)  # custo exato do pior caso com saída mínima (256)
    llm.call(budget=llm.RunBudget(max_tokens=100_000, max_calls=5, max_cost_micro_usd=worst + 1), **args)
    with pytest.raises(llm.LimitReached):
        llm.call(budget=llm.RunBudget(max_tokens=100_000, max_calls=5, max_cost_micro_usd=worst - 1), **args)
