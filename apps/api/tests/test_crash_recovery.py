"""Auditoria A07 (revalidação final): queda REAL do processo do worker em cada fronteira de uma missão.

Um worker num processo separado morre com `os._exit(137)` (equivalente a kill -9) no ponto escolhido; depois a lease
vence e outro worker retoma. Em todos os pontos: a missão termina, as etapas são criadas UMA vez e nenhuma reserva de
custo fica perdida — se a queda foi durante a chamada ao modelo, o custo máximo reservado entra no ledger."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import select, text

from aicommerce.db import session_scope
from aicommerce.faults import EXIT_CODE
from aicommerce.models import AgentTask, AISpendHold, AIUsage
from tests.test_missions import drain, start

API_DIR = pathlib.Path(__file__).resolve().parents[1]
WORKER = (
    "from aicommerce import worker\n"
    "from aicommerce.ai import runtime\n"
    "while worker.run_once() or runtime.advance_missions():\n"
    "    pass\n"
)
POINTS = [
    "worker.after_claim",
    "runtime.after_context",
    "llm.after_hold",
    "llm.after_provider",
    "llm.after_record",
    "runtime.before_persist_commit",
    "mission.after_children_done",
    "mission.after_summary_call",
]


def crash_worker_at(point: str) -> int:
    env = {**os.environ, "AIC_FAULT_POINT": point}
    return subprocess.run([sys.executable, "-c", WORKER], cwd=API_DIR, env=env, timeout=120).returncode


def time_passes() -> None:
    """A lease do worker morto vence e a reserva aberta expira (o que levaria minutos na vida real)."""
    with session_scope() as db:
        db.execute(text("UPDATE agent_tasks SET updated_at = now() - interval '10 minutes' WHERE state = 'RUNNING'"))
        db.execute(text("UPDATE ai_spend_holds SET expires_at = now() - interval '1 second'"))


@pytest.mark.parametrize("point", POINTS)
def test_mission_survives_a_real_worker_crash_at_each_boundary(client, auth, point):
    drain()  # fila vazia: o processo que vai cair pega exatamente esta missão
    mid = uuid.UUID(start(client, auth("operator"), f"avaliar suporte de notebook ({point})", steps=3))
    assert crash_worker_at(point) == EXIT_CODE  # o processo caiu de verdade NAQUELE ponto
    time_passes()
    drain()
    with session_scope() as db:
        mission = db.get(AgentTask, mid)
        kids = db.execute(select(AgentTask).where(AgentTask.parent_task_id == mid)).scalars().all()
        task_ids = [mid, *(k.id for k in kids)]
        open_holds = db.execute(select(AISpendHold).where(AISpendHold.task_id.in_(task_ids))).scalars().all()
        expired = (
            db.execute(select(AIUsage).where(AIUsage.task_id.in_(task_ids), AIUsage.stop_reason == "HOLD_EXPIRED"))
            .scalars()
            .all()
        )
    assert mission.state == "COMPLETED" and (mission.result or {}).get("summary")
    assert len(kids) == 3 and all(k.state == "COMPLETED" for k in kids)  # etapas criadas uma única vez
    assert open_holds == []  # nenhuma reserva esquecida
    if point in ("llm.after_hold", "llm.after_provider"):
        # caiu com a chamada em andamento: o custo máximo reservado foi lançado para a tarefa (teto nunca subconta)
        assert len(expired) == 1 and expired[0].task_id == mid and expired[0].request_cost_micro_usd > 0
    else:
        assert expired == []


def test_fault_points_are_inert_outside_test_environments(monkeypatch):
    from aicommerce import faults
    from aicommerce.config import get_settings

    monkeypatch.setenv("AIC_FAULT_POINT", "llm.after_hold")
    monkeypatch.setattr(get_settings(), "environment", get_settings().environment.__class__.PRODUCTION)
    faults.point("llm.after_hold")  # não derruba o processo em produção
    monkeypatch.setattr(get_settings(), "environment", get_settings().environment.__class__.STAGING)
    faults.point("llm.after_hold")
