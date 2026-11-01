"""Worker persistente: consome agent_tasks QUEUED e execution_jobs (side effects) com FOR UPDATE SKIP LOCKED.

Executor DRY_RUN determinístico — sem LLM nesta fase (AI-001/AI-002 trazem o adapter). Tipos de tarefa
não suportados falham com erro tipado (nunca sucesso simulado). Tarefas RUNNING com lease vencida são
marcadas FAILED para reconciliação humana antes de qualquer retry (REQ-DEV-008).

Uso: python -m aicommerce.worker [--once]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import signal
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce import faults
from aicommerce.ai import llm, runtime
from aicommerce.ai.specs import SPECS
from aicommerce.commerce import orders, webhooks
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.governance import executor, killswitch
from aicommerce.models import AgentTask
from aicommerce.observability import beat, configure_json_logging
from aicommerce.office import events
from aicommerce.office.contract import AgentState
from aicommerce.workforce.tasks import TaskState, transition

log = logging.getLogger("aicommerce.worker")
LEASE = dt.timedelta(minutes=5)
MAX_RESUMES = (
    1  # uma retomada automática por tarefa de IA; depois falha tipada (evita laço em tarefa que derruba o worker)
)


def _mock_heartbeat(task: AgentTask) -> dict[str, Any]:
    return {"summary": f"{task.agent_id} respondeu ao heartbeat", "simulated": True}


def _mock_research(task: AgentTask) -> dict[str, Any]:
    topic = str(task.input.get("topic", "sem tema"))
    return {
        "summary": f"Pesquisa SIMULADA sobre '{topic}' — fixture determinística, sem fontes reais.",
        "simulated": True,
        "sources": [],
        "evidence_quality": "NONE_SIMULATED",
    }


EXECUTORS: dict[str, tuple[str | None, Callable[[AgentTask], dict[str, Any]]]] = {
    # kind: (ferramenta mock exibida como TOOL_CALL, executor)
    "foundation.heartbeat": (None, _mock_heartbeat),
    "research.mock": ("mock.trend_source", _mock_research),
    "research.hunt": (None, lambda task: _hunt(task)),
}


def _hunt(task: AgentTask) -> dict[str, Any]:
    from aicommerce.ai import hunt

    return hunt.run_task(task)


def expire_stale(db: Session, now: dt.datetime) -> int:
    stale = (
        db.execute(
            select(AgentTask)
            .where(AgentTask.state == TaskState.RUNNING.value, AgentTask.updated_at < now - LEASE)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for t in stale:
        cp = runtime.last_checkpoint(db, t.id)
        node = cp.node if cp else None
        if t.kind == "mission.plan" and node in ("children_spawned", "children_done"):
            # caiu no resumo: as etapas já existem — retoma do checkpoint (o Atlas volta a resumir), sem recriar etapas
            runtime.checkpoint(db, t.id, "resumed_after_lease", {"from": node})
            transition(db, t, TaskState.WAITING_EXTERNAL, detail={"resumed_from": node})
        elif t.kind in SPECS and t.attempts < MAX_RESUMES + 1:
            # tarefa de IA não tem efeito externo antes do passo "persisted" (atômico): refazer é seguro
            runtime.checkpoint(db, t.id, "resumed_after_lease", {"from": node, "attempt": t.attempts})
            transition(db, t, TaskState.QUEUED, detail={"resumed_from": node or "início"})
        else:
            transition(
                db,
                t,
                TaskState.FAILED,
                error="LEASE_EXPIRED: lease do worker expirou; reconciliar antes de retry",
                detail={"error_kind": "LEASE_EXPIRED"},
            )
    return len(stale)


def claim(db: Session) -> AgentTask | None:
    task = db.execute(
        select(AgentTask)
        .where(AgentTask.state == TaskState.QUEUED.value)
        .order_by(AgentTask.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if task:
        task.attempts += 1
        transition(db, task, TaskState.RUNNING)
    return task


def run_once() -> bool:
    """Processa no máximo uma tarefa. Retorna True se havia trabalho."""
    with session_scope() as db:
        expire_stale(db, dt.datetime.now(dt.UTC))
        llm.settle_expired_holds(db)
        if killswitch.state(db).get("queues_frozen") and killswitch.is_active(db):
            return False
        task = claim(db)
        if task is None:
            return False
        task_id, kind = task.id, task.kind
    faults.point("worker.after_claim")
    if kind in SPECS:  # tarefas de agente com LLM: runtime gerencia as próprias transações
        try:
            runtime.run(task_id)
        except Exception as e:
            runtime._fail(task_id, "RUNTIME", f"{type(e).__name__}: {e}")
        return True
    with session_scope() as db:
        task = db.get(AgentTask, task_id, with_for_update=True)
        entry = EXECUTORS.get(task.kind)
        if entry is None:
            transition(
                db,
                task,
                TaskState.FAILED,
                error=f"UNSUPPORTED: tipo de tarefa '{task.kind}'",
                detail={"error_kind": "UNSUPPORTED"},
            )
            return True
        tool, fn = entry
        if tool:
            events.emit(
                db,
                "agent.task.state_changed",
                agent_id=task.agent_id,
                task_id=task.id,
                state=AgentState.TOOL_CALL,
                payload={"tool": tool, "simulated": True},
            )
    with session_scope() as db:
        task = db.get(AgentTask, task_id, with_for_update=True)
        if task.state != TaskState.RUNNING.value:  # cancelada durante a execução: resultado descartado
            return True
        try:
            result = fn(task)
        except Exception as e:
            transition(db, task, TaskState.FAILED, error=f"{type(e).__name__}: {e}", detail={"error_kind": "EXECUTOR"})
        else:
            transition(db, task, TaskState.COMPLETED, result=result)
    return True


def _expire_gates() -> None:
    from aicommerce.governance import gates

    with session_scope() as db:
        gates.expire_due(db)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    configure_json_logging()
    log.info("worker iniciado em modo %s", get_settings().mode.value)
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    failures = 0
    while not stop:
        worked, failures = cycle(failures)
        if args.once:
            break
        if failures:
            time.sleep(backoff_s(failures))
        elif not worked:
            time.sleep(0.5)


MAX_BACKOFF_S = 30.0


def backoff_s(failures: int) -> float:
    return min(MAX_BACKOFF_S, 0.5 * 2 ** min(failures - 1, 6))


def _dispatch_routines() -> bool:
    from aicommerce.workforce import routines

    with session_scope() as db:
        return routines.dispatch_due(db) > 0


def cycle(failures: int = 0) -> tuple[bool, int]:
    """Um ciclo do worker. Falha de infraestrutura (ex.: banco fora) não derruba o processo: registra e recua.

    Todo trabalho é transacional e idempotente (lease, idempotency_key), então repetir após a volta do banco é seguro.
    """
    try:
        beat()
        worked = run_once()
        worked = executor.execute_next() or worked
        worked = executor.reconcile_next() or worked
        worked = webhooks.process_next() or worked
        worked = orders.process_fiscal_next() or worked
        worked = runtime.advance_missions() or worked
        worked = _dispatch_routines() or worked
        executor.sweep_expired()
        _expire_gates()
    except Exception as e:
        failures += 1
        log.error(
            "ciclo do worker falhou (%s); nova tentativa em %.1f s",
            type(e).__name__,
            backoff_s(failures),
            extra={"failures": failures},
        )
        return False, failures
    if failures:
        log.info("worker recuperado após %d falha(s)", failures)
    return worked, 0


if __name__ == "__main__":
    main()
