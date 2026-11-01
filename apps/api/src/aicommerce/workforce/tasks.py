"""Máquina de estados de tarefas de agentes (SDD §7).

CREATED→QUEUED→RUNNING→WAITING_EXTERNAL|WAITING_APPROVAL→RESUMED→COMPLETED|FAILED|CANCELLED
Transições inválidas levantam erro; cada transição gera evento de escritório (fonte da cena 3D).
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import audit
from aicommerce.models import Agent, AgentTask
from aicommerce.office import events
from aicommerce.office.contract import AgentState, EventType


class TaskState(enum.StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RESUMED = "RESUMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


T = TaskState
TRANSITIONS: dict[TaskState, set[TaskState]] = {
    T.CREATED: {T.QUEUED, T.CANCELLED},
    T.QUEUED: {T.RUNNING, T.CANCELLED},
    # RUNNING → QUEUED: retomada após queda do worker (tarefa de IA sem efeito externo antes de persistir)
    T.RUNNING: {T.WAITING_EXTERNAL, T.WAITING_APPROVAL, T.COMPLETED, T.FAILED, T.CANCELLED, T.QUEUED},
    T.WAITING_EXTERNAL: {T.RESUMED, T.FAILED, T.CANCELLED},
    T.WAITING_APPROVAL: {T.RESUMED, T.FAILED, T.CANCELLED},
    T.RESUMED: {T.RUNNING, T.FAILED, T.CANCELLED},
    T.COMPLETED: set(),
    T.FAILED: set(),
    T.CANCELLED: set(),
}

PRESENCE_FOR: dict[TaskState, AgentState] = {
    T.CREATED: AgentState.PLANNING,
    T.QUEUED: AgentState.PLANNING,
    T.RUNNING: AgentState.WORKING,
    T.WAITING_EXTERNAL: AgentState.TOOL_CALL,
    T.WAITING_APPROVAL: AgentState.WAITING_APPROVAL,
    T.RESUMED: AgentState.WORKING,
    T.COMPLETED: AgentState.COMPLETED,
    T.FAILED: AgentState.ERROR,
    T.CANCELLED: AgentState.IDLE,
}

EVENT_FOR: dict[TaskState, EventType] = {
    T.RUNNING: EventType.AGENT_TASK_STARTED,
    T.COMPLETED: EventType.AGENT_TASK_COMPLETED,
    T.FAILED: EventType.AGENT_TASK_FAILED,
}


class InvalidTransition(Exception):
    pass


class UnknownAgent(Exception):
    pass


def create(db: Session, *, agent_id: str, kind: str, input_: dict[str, Any], actor: uuid.UUID | None) -> AgentTask:
    if db.get(Agent, agent_id) is None:
        raise UnknownAgent(agent_id)
    task = AgentTask(
        agent_id=agent_id,
        kind=kind,
        state=T.CREATED.value,
        mode=get_settings().mode.value,
        input=input_,
        created_by=actor,
    )
    db.add(task)
    db.flush()
    events.emit(
        db,
        EventType.AGENT_TASK_CREATED,
        agent_id=agent_id,
        task_id=task.id,
        state=PRESENCE_FOR[T.CREATED],
        payload={"kind": kind},
    )
    audit.record(
        db,
        actor_type="user" if actor else "system",
        actor_id=str(actor or "system"),
        action="agent_task.created",
        subject_type="agent_task",
        subject_id=str(task.id),
        payload={"agent_id": agent_id, "kind": kind},
    )
    transition(db, task, T.QUEUED)
    return task


def transition(
    db: Session,
    task: AgentTask,
    to: TaskState,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AgentTask:
    current = TaskState(task.state)
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(f"{current.value} → {to.value} não permitido")
    task.state = to.value
    if result is not None:
        task.result = result
    if error is not None:
        task.error = error
    db.flush()  # version_id_col: atualização concorrente conflitante levanta StaleDataError
    events.emit(
        db,
        EVENT_FOR.get(to, EventType.AGENT_TASK_STATE_CHANGED),
        agent_id=task.agent_id,
        task_id=task.id,
        state=PRESENCE_FOR[to],
        payload={"task_state": to.value, "from": current.value, **(detail or {})},
    )
    return task
