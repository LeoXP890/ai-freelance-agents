"""Orquestração (seção 5.2 da finalização): missão cancelada ENQUANTO o modelo responde não produz efeito — nada é
gravado depois do cancelamento, nenhuma etapa nova nasce, o resumo não roda — e o custo já incorrido fica lançado."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from aicommerce.ai import llm
from aicommerce.db import session_scope
from aicommerce.models import AgentTask, AISpendHold, AIUsage
from tests.test_missions import drain, start


class CancelOnCall(llm.MockLLMProvider):
    """Mock que, na N-ésima chamada, cancela a missão pela API (como o operador faria) antes de responder."""

    def __init__(self, client, headers, n):
        super().__init__()
        self.client, self.headers, self.n, self.count, self.mission = client, headers, n, 0, None

    def complete(self, req):
        self.count += 1
        if self.count == self.n:
            r = self.client.post(f"/v1/missions/{self.mission}/cancel", headers=self.headers, json={"reason": "teste"})
            assert r.status_code == 200, r.text
        return super().complete(req)


def run_with(provider) -> None:
    llm.set_provider(provider)
    try:
        drain()
    finally:
        llm.set_provider(None)


def state(mid):
    with session_scope() as db:
        m = db.get(AgentTask, mid)
        kids = db.execute(select(AgentTask).where(AgentTask.parent_task_id == mid)).scalars().all()
        ids = [mid, *(k.id for k in kids)]
        paid = db.execute(select(AIUsage).where(AIUsage.task_id.in_(ids), AIUsage.request_cost_micro_usd > 0)).all()
        holds = db.execute(select(AISpendHold).where(AISpendHold.task_id.in_(ids))).all()
        return m, kids, len(paid), len(holds)


def test_cancel_while_atlas_plans_creates_no_steps(client, auth):
    h = auth("operator")
    drain()
    p = CancelOnCall(client, h, n=1)
    p.mission = start(client, h, "avaliar porta-copos de cortiça")
    run_with(p)
    m, kids, paid, holds = state(uuid.UUID(p.mission))
    assert m.state == "CANCELLED" and kids == []  # o plano respondido depois do cancelamento não virou etapas
    assert paid == 1 and holds == 0  # a chamada feita continua contabilizada; nenhuma reserva aberta


def test_cancel_during_a_step_discards_its_result_and_skips_the_summary(client, auth):
    h = auth("operator")
    drain()
    p = CancelOnCall(client, h, n=2)  # 1ª chamada = plano do Atlas; 2ª = primeira etapa
    p.mission = start(client, h, "avaliar suporte de celular para carro")
    run_with(p)
    m, kids, paid, holds = state(uuid.UUID(p.mission))
    assert m.state == "CANCELLED" and kids and all(k.state == "CANCELLED" for k in kids)
    assert all(k.result is None for k in kids)  # resultado da etapa em andamento descartado
    with session_scope() as db:
        summaries = db.execute(
            select(AIUsage).where(AIUsage.task_id == m.id, AIUsage.purpose == "mission.summarize")
        ).all()
    assert summaries == [] and paid == 2 and holds == 0
