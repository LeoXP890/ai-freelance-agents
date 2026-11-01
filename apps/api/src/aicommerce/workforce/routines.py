"""Rotinas dos agentes: tarefas que se repetem sozinhas (todo dia num horário, ou a cada N horas).

A rotina só CRIA a tarefa no horário, pelo mesmo caminho do pedido manual (tasks.create): orçamento de IA,
ferramentas autorizadas, auditoria e aprovação humana de qualquer efeito externo continuam valendo. Só tipos de
tarefa de pesquisa/relatório entram (lista fechada abaixo). Kill switch ativo: nada dispara. Se a execução anterior
ainda não terminou, a vez é pulada (sem fila acumulada).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.governance import audit, killswitch
from aicommerce.models import AgentRoutine, AgentTask
from aicommerce.workforce import tasks
from aicommerce.workforce.tasks import TaskState

TZ = ZoneInfo("America/Sao_Paulo")
MAX_ROUTINES = 30
OPEN = {
    s.value
    for s in (
        TaskState.CREATED,
        TaskState.QUEUED,
        TaskState.RUNNING,
        TaskState.WAITING_EXTERNAL,
        TaskState.WAITING_APPROVAL,
        TaskState.RESUMED,
    )
}

# tipo de tarefa → (agente, rótulo, campo de entrada, rótulo do campo). Só pesquisa e relatório (sem efeito externo).
KINDS: dict[str, tuple[str, str, str | None, str | None]] = {
    "research.hunt": ("luna", "Caçar produtos (em alta × Dropi × Mercado Livre, com lucro)", None, None),
    "research.opportunities": ("luna", "Pesquisar oportunidades de produto", "topic", "Tema"),
    "supplier.discover": ("nexus", "Procurar fornecedores", "category", "Categoria de produto"),
    "supplier.shortlist": ("nexus", "Comparar fornecedores cadastrados", "category", "Categoria de produto"),
    "compliance.review": ("clara", "Revisão de compliance", "question", "Pergunta"),
    "mission.plan": ("atlas", "Missão completa (planejar e executar)", "goal", "Objetivo"),
    "finance.brief": ("finn", "Resumo financeiro", None, None),
    "analytics.report": ("orion", "Relatório de vendas", None, None),
    "logistics.sla_check": ("logan", "Checagem de prazos de entrega", None, None),
}


class RoutineError(Exception):
    http = 409


def catalog() -> list[dict[str, Any]]:
    return [
        {"kind": k, "agent_id": a, "label": label, "field": f, "field_label": fl}
        for k, (a, label, f, fl) in KINDS.items()
    ]


def next_run(r: AgentRoutine, after: dt.datetime) -> dt.datetime:
    """Próximo horário estritamente depois de `after` (diária: horário de Brasília; intervalo: a cada N horas)."""
    if r.schedule == "INTERVAL":
        return after + dt.timedelta(hours=int(r.interval_hours or 24))
    hh, mm = (int(x) for x in str(r.time_of_day).split(":"))
    local = after.astimezone(TZ)
    cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= local:
        cand = (local + dt.timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return cand.astimezone(dt.UTC)


def _validate(
    agent_id: str, kind: str, schedule: str, time_of_day: str | None, interval_hours: int | None, text: str | None
) -> dict[str, Any]:
    spec = KINDS.get(kind)
    if spec is None:
        raise RoutineError(f"tipo de tarefa não permitido em rotina: {kind}")
    if spec[0] != agent_id:
        raise RoutineError(f"'{spec[1]}' é trabalho do agente {spec[0]}, não de {agent_id}")
    if schedule == "DAILY":
        if not time_of_day or len(time_of_day) != 5 or interval_hours is not None:
            raise RoutineError("rotina diária precisa do horário (HH:MM)")
        hh, _, mm = time_of_day.partition(":")
        if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            raise RoutineError("horário inválido (HH:MM)")
    elif schedule == "INTERVAL":
        if interval_hours is None or not 2 <= interval_hours <= 168 or time_of_day is not None:
            raise RoutineError("intervalo entre 2 e 168 horas")
    else:
        raise RoutineError("agenda inválida: DAILY ou INTERVAL")
    field = spec[2]
    if field is None:
        return {}
    value = (text or "").strip()
    if len(value) < 3:
        raise RoutineError(f"informe {spec[3].lower()} (mín. 3 caracteres)")
    return {field: value[:500]}


def create(
    db: Session,
    *,
    agent_id: str,
    kind: str,
    schedule: str,
    time_of_day: str | None = None,
    interval_hours: int | None = None,
    text: str | None = None,
    title: str | None = None,
    actor: uuid.UUID | None,
    now: dt.datetime | None = None,
) -> AgentRoutine:
    inp = _validate(agent_id, kind, schedule, time_of_day, interval_hours, text)
    if (db.query(AgentRoutine).count()) >= MAX_ROUTINES:
        raise RoutineError(f"limite de {MAX_ROUTINES} rotinas")
    r = AgentRoutine(
        agent_id=agent_id,
        kind=kind,
        title=(title or KINDS[kind][1])[:120],
        input=inp,
        schedule=schedule,
        time_of_day=time_of_day if schedule == "DAILY" else None,
        interval_hours=interval_hours if schedule == "INTERVAL" else None,
        enabled=True,
        created_by=actor,
    )
    r.next_run_at = next_run(r, now or dt.datetime.now(dt.UTC))
    db.add(r)
    db.flush()
    audit.record(
        db,
        actor_type="user" if actor else "system",
        actor_id=str(actor or "system"),
        action="agent_routine.created",
        subject_type="agent_routine",
        subject_id=str(r.id),
        payload={
            "agent_id": agent_id,
            "kind": kind,
            "schedule": schedule,
            "time_of_day": r.time_of_day,
            "interval_hours": r.interval_hours,
            "input": inp,
        },
    )
    return r


def update(
    db: Session,
    routine_id: uuid.UUID,
    *,
    actor: uuid.UUID | None,
    enabled: bool | None = None,
    schedule: str | None = None,
    time_of_day: str | None = None,
    interval_hours: int | None = None,
    text: str | None = None,
    title: str | None = None,
    now: dt.datetime | None = None,
) -> AgentRoutine:
    r = db.get(AgentRoutine, routine_id, with_for_update=True)
    if r is None:
        raise RoutineError("rotina inexistente")
    if schedule is not None or text is not None:
        sched = schedule or r.schedule
        tod = time_of_day if schedule is not None else r.time_of_day
        ih = interval_hours if schedule is not None else r.interval_hours
        field = KINDS[r.kind][2]
        current = r.input.get(field) if field else None
        r.input = _validate(r.agent_id, r.kind, sched, tod, ih, text if text is not None else current)
        r.schedule, r.time_of_day, r.interval_hours = (
            sched,
            tod if sched == "DAILY" else None,
            ih if sched == "INTERVAL" else None,
        )
    if title:
        r.title = title[:120]
    if enabled is not None:
        r.enabled = enabled
    r.next_run_at = next_run(r, now or dt.datetime.now(dt.UTC)) if r.enabled else None
    audit.record(
        db,
        actor_type="user" if actor else "system",
        actor_id=str(actor or "system"),
        action="agent_routine.updated",
        subject_type="agent_routine",
        subject_id=str(r.id),
        payload={
            "enabled": r.enabled,
            "schedule": r.schedule,
            "time_of_day": r.time_of_day,
            "interval_hours": r.interval_hours,
            "input": r.input,
        },
    )
    return r


def delete(db: Session, routine_id: uuid.UUID, *, actor: uuid.UUID | None) -> None:
    r = db.get(AgentRoutine, routine_id, with_for_update=True)
    if r is None:
        raise RoutineError("rotina inexistente")
    audit.record(
        db,
        actor_type="user" if actor else "system",
        actor_id=str(actor or "system"),
        action="agent_routine.deleted",
        subject_type="agent_routine",
        subject_id=str(r.id),
        payload={"agent_id": r.agent_id, "kind": r.kind},
    )
    db.delete(r)


def _fire(db: Session, r: AgentRoutine, now: dt.datetime, *, manual: bool) -> AgentTask | None:
    last = db.get(AgentTask, r.last_task_id) if r.last_task_id else None
    if last is not None and last.state in OPEN:
        r.last_outcome = "PULADA_EM_ANDAMENTO"
        return None
    task = tasks.create(
        db, agent_id=r.agent_id, kind=r.kind, input_={**r.input, "routine_id": str(r.id)}, actor=r.created_by
    )
    r.last_task_id, r.last_run_at, r.last_outcome = task.id, now, "MANUAL" if manual else "DISPARADA"
    return task


def run_now(db: Session, routine_id: uuid.UUID, *, actor: uuid.UUID | None) -> AgentTask:
    r = db.get(AgentRoutine, routine_id, with_for_update=True)
    if r is None:
        raise RoutineError("rotina inexistente")
    if killswitch.is_active(db):
        raise RoutineError("parada de emergência ativa: rotinas suspensas")
    task = _fire(db, r, dt.datetime.now(dt.UTC), manual=True)
    if task is None:
        raise RoutineError("a execução anterior desta rotina ainda não terminou")
    return task


def dispatch_due(db: Session, now: dt.datetime | None = None, limit: int = 5) -> int:
    """Chamado pelo worker: dispara as rotinas vencidas. Retorna quantas tarefas criou."""
    now = now or dt.datetime.now(dt.UTC)
    due = (
        db.execute(
            select(AgentRoutine)
            .where(AgentRoutine.enabled.is_(True), AgentRoutine.next_run_at <= now)
            .order_by(AgentRoutine.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    if not due:
        return 0
    if killswitch.is_active(db):
        for r in due:  # sem acumular: a próxima vez é a da agenda
            r.last_outcome, r.next_run_at = "PULADA_EMERGENCIA", next_run(r, now)
        return 0
    created = 0
    for r in due:
        created += _fire(db, r, now, manual=False) is not None
        r.next_run_at = next_run(r, now)
    return created


def out(r: AgentRoutine) -> dict[str, Any]:
    field = KINDS.get(r.kind, (None, None, None, None))[2]
    return {
        "id": str(r.id),
        "agent_id": r.agent_id,
        "kind": r.kind,
        "title": r.title,
        "text": r.input.get(field) if field else None,
        "schedule": r.schedule,
        "time_of_day": r.time_of_day,
        "interval_hours": r.interval_hours,
        "enabled": r.enabled,
        "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
        "last_task_id": str(r.last_task_id) if r.last_task_id else None,
        "last_outcome": r.last_outcome,
    }
