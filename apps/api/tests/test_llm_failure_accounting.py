"""Falha depois que o provedor processou a chamada também custa (finalização, seção 5.1): o ledger recebe o uso real
informado na resposta (corte por limite, recusa, JSON inválido); se não dá para saber (timeout depois do envio ou
erro inesperado), lança o pior caso reservado; erro antes de processar (429/5xx) não custa.
Reserva nunca fica aberta."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from aicommerce.ai import llm
from aicommerce.db import session_scope
from aicommerce.models import AISpendHold, AIUsage
from tests.test_ai_budget import SCHEMA

USAGE = {"promptTokenCount": 1200, "candidatesTokenCount": 800, "thoughtsTokenCount": 100}


def gemini(reply):
    def post(url, headers, body, timeout):
        if isinstance(reply, Exception):
            raise reply
        return reply

    return llm.GoogleLLMProvider(api_key="k", post=post)


def call_with(provider, monkeypatch):
    monkeypatch.setattr(llm, "route", lambda **k: llm.Route("small", "gemini-2.5-flash", "teste"))
    llm.set_provider(provider)
    task = uuid.uuid4()
    try:
        with pytest.raises(llm.LLMError) as err:
            llm.call(
                agent_id="luna",
                purpose="falha",
                spec_tier="small",
                risk="LOW",
                attempt=1,
                system="s",
                user="pergunta " + task.hex,
                schema=SCHEMA,
                mock_output=None,
                task_id=task,
                budget=llm.RunBudget(max_tokens=100_000, max_calls=5, max_cost_micro_usd=10_000_000),
            )
    finally:
        llm.set_provider(None)
    with session_scope() as db:
        rows = db.execute(select(AIUsage).where(AIUsage.task_id == task)).scalars().all()
        holds = db.execute(select(AISpendHold).where(AISpendHold.task_id == task)).scalars().all()
    assert holds == []
    return err.value, rows


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{"}]}}]}, "TRUNCATED"),
        ({"candidates": [{"finishReason": "SAFETY"}]}, "REFUSAL"),
        ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "isso não é json"}]}}]}, "LLM_ERROR"),
        ({"promptFeedback": {"blockReason": "OTHER"}}, "REFUSAL"),
    ],
)
def test_answered_failures_charge_the_real_usage(monkeypatch, body, kind):
    err, rows = call_with(gemini((200, {**body, "usageMetadata": USAGE})), monkeypatch)
    assert err.kind == kind and len(rows) == 1
    real = llm.cost_micro_usd("gemini-2.5-flash", 1200, 0, 900)  # entrada + saída + raciocínio
    assert rows[0].request_cost_micro_usd == real and rows[0].stop_reason == kind


def test_timeout_after_sending_charges_the_reserved_worst_case(monkeypatch):
    err, rows = call_with(gemini(TimeoutError("sem resposta")), monkeypatch)
    assert err.uncertain and len(rows) == 1
    assert rows[0].stop_reason == "UNCERTAIN_TRANSIENT" and rows[0].request_cost_micro_usd > 0


def test_rejected_before_processing_costs_nothing(monkeypatch):
    err, rows = call_with(gemini((429, {"error": {"message": "quota"}})), monkeypatch)
    assert err.kind == "TRANSIENT" and not err.uncertain and rows == []
