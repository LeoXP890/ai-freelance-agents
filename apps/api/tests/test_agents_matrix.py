"""Matriz dos agentes com o provedor SIMULADO (finalização do desenvolvimento §7): cada tarefa de cada agente passa
pelos mesmos cenários de falha, pelo caminho real (API → fila → worker → runtime):

* erro temporário do provedor uma vez → nova tentativa → concluída; a tentativa que falhou fica no ledger;
* erro temporário persistente → falha TRANSIENT depois de 1 + llm_transient_retries chamadas (todas cobradas);
* resposta fora do contrato em todas as tentativas → OUTPUT_INVALID, nada persistido como resultado;
* dados insuficientes → INPUT, sem nenhuma chamada ao modelo (não gasta);
* erro definitivo do provedor → falha na hora, sem insistir.

Cancelamento e recuperação após queda do processo: tests/test_cancellation.py e tests/test_crash_recovery.py."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from aicommerce.ai import evals, llm
from aicommerce.ai.specs import SPECS
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.models import AgentTask, AIUsage
from tests.test_missions import drain, start
from tests.test_orders import order, shop  # noqa: F401 — fixture: anúncio publicado pelo fluxo real

# tarefas disparadas pela API/worker (o resumo da missão é chamado pelo orquestrador, coberto em test_missions)
KINDS = sorted(k for k in SPECS if k != "mission.summarize")
# trabalham sobre os dados do sistema (ou, a Luna, com tema padrão "casa e cozinha") — sem entrada obrigatória
NO_INPUT_NEEDED = {
    "finance.brief",
    "analytics.report",
    "logistics.sla_check",
    "supplier.shortlist",
    "channel.plan",
    "research.opportunities",
}


class Script:
    """Provedor simulado com roteiro: 'T' = erro temporário, 'P' = erro definitivo, 'X' = saída fora do contrato,
    'ok' = resposta simulada correta. Depois do roteiro, responde 'ok'."""

    name, simulated = "roteiro", True

    def __init__(self, *plan: str):
        self.plan, self.calls = list(plan), 0

    def complete(self, req):
        self.calls += 1
        step = self.plan.pop(0) if self.plan else "ok"
        if step == "T":
            charged = llm.LLMResult(
                output=None,
                model=req.model,
                input_tokens=10,
                cached_tokens=0,
                output_tokens=0,
                stop_reason="error",
                provider=self.name,
            )
            raise llm.LLMTransient("HTTP 503 simulado", usage=charged)
        if step == "P":
            raise llm.LLMError("HTTP 400 simulado: requisição recusada")
        out = {"lixo": True} if step == "X" else req.mock_output
        return llm.LLMResult(
            output=out,
            model=req.model,
            input_tokens=100,
            cached_tokens=0,
            output_tokens=50,
            stop_reason="end_turn",
            provider=self.name,
        )


@pytest.fixture
def data(client, auth, shop):  # noqa: F811
    order(client, shop)
    start(client, auth("operator"), "avaliar luminária de mesa", steps=2)
    drain()
    inputs = {}
    with session_scope() as db:
        for kind, inp in evals.TASK_CASES:
            inputs.setdefault(kind, inp(db) if callable(inp) else inp)
    assert all(inputs.get(k) is not None for k in KINDS), inputs
    return inputs


@pytest.fixture
def script(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_retry_backoff_s", 0.0)
    holder = {}

    def use(*plan):
        holder["p"] = Script(*plan)
        llm.set_provider(holder["p"])
        return holder["p"]

    yield use
    llm.set_provider(None)


def run(client, h, kind, inp):
    inp = (
        {**inp, "_nonce": uuid.uuid4().hex}
        if kind != "mission.plan"
        else {**inp, "goal": f"{inp['goal']} {uuid.uuid4().hex[:6]}"}
    )
    r = client.post("/v1/agent-tasks", headers=h, json={"agent_id": SPECS[kind].agent_id, "kind": kind, "input": inp})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    drain()
    with session_scope() as db:
        t = db.get(AgentTask, uuid.UUID(tid))
        usage = db.execute(select(func.count()).select_from(AIUsage).where(AIUsage.task_id == t.id)).scalar_one()
        return t.state, (t.detail or {}).get("error_kind") if hasattr(t, "detail") else None, t.error, t.result, usage


@pytest.mark.parametrize("kind", KINDS)
def test_transient_once_is_retried_and_completes(client, auth, data, script, kind):
    p = script("T")
    state, _, err, _, usage = run(client, auth("operator"), kind, data[kind])
    assert state == "COMPLETED", err
    assert p.calls >= 2 and usage >= 2  # a tentativa que falhou também ficou no ledger


@pytest.mark.parametrize("kind", KINDS)
def test_persistent_transient_fails_after_bounded_retries(client, auth, data, script, kind):
    p = script(*["T"] * 10)
    state, _, err, result, usage = run(client, auth("operator"), kind, data[kind])
    assert state == "FAILED" and err.startswith("TRANSIENT"), err
    assert p.calls == 1 + get_settings().llm_transient_retries and usage == p.calls and not result


@pytest.mark.parametrize("kind", KINDS)
def test_invalid_output_every_time_fails_without_result(client, auth, data, script, kind):
    p = script(*["X"] * 10)
    state, _, err, result, usage = run(client, auth("operator"), kind, data[kind])
    assert state == "FAILED" and err.startswith("OUTPUT_INVALID"), err
    assert p.calls == 2 and usage == 2 and not result  # corrige uma vez (modelo maior) e desiste; tudo cobrado


@pytest.mark.parametrize("kind", KINDS)
def test_permanent_error_fails_at_once(client, auth, data, script, kind):
    p = script("P")
    state, _, err, _, _ = run(client, auth("operator"), kind, data[kind])
    assert state == "FAILED" and p.calls == 1, err


@pytest.mark.parametrize("kind", sorted(set(KINDS) - NO_INPUT_NEEDED))
def test_insufficient_data_fails_before_any_model_call(client, auth, script, kind):
    p = script()
    r = client.post(
        "/v1/agent-tasks", headers=auth("operator"), json={"agent_id": SPECS[kind].agent_id, "kind": kind, "input": {}}
    )
    assert r.status_code == 201, r.text
    drain()
    with session_scope() as db:
        t = db.get(AgentTask, uuid.UUID(r.json()["id"]))
        assert t.state == "FAILED" and t.error.startswith("INPUT"), t.error
    assert p.calls == 0  # não gastou nada


def test_matrix_covers_all_13_agents():
    assert len({SPECS[k].agent_id for k in KINDS} | {"atlas"}) == 13
