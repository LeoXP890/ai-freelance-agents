"""Emissão de eventos do escritório e projeção de presença.

Inserções são serializadas por advisory lock transacional: a ordem de `seq` coincide com a ordem de
commit, então um leitor que pagina por `seq > cursor` nunca perde um evento que commitou "atrasado".
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.models import Agent, AgentPresence, OfficeEvent, SystemFlag
from aicommerce.office.contract import AgentState, EventType, OfficeEventV1, PresenceSnapshotV1, PresenceV1


def emit(
    db: Session,
    type_: str | EventType,
    *,
    agent_id: str | None = None,
    task_id: uuid.UUID | None = None,
    state: AgentState | str | None = None,
    payload: dict[str, Any] | None = None,
) -> OfficeEventV1:
    s = get_settings()
    type_value = EventType(type_).value
    state_value = AgentState(state).value if state else None
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('aic_office_events'))"))
    ev = OfficeEvent(
        event_id=uuid.uuid4(),
        type=type_value,
        schema_version=1,
        agent_id=agent_id,
        task_id=task_id,
        state=state_value,
        mode=s.mode.value,
        payload=payload or {},
        occurred_at=dt.datetime.now(dt.UTC),
    )
    db.add(ev)
    db.flush()
    if agent_id and state_value:
        db.execute(
            update(AgentPresence)
            .where(AgentPresence.agent_id == agent_id, AgentPresence.last_seq < ev.seq)
            .values(state=state_value, task_id=task_id, last_seq=ev.seq, updated_at=ev.occurred_at)
        )
    return to_contract(ev)


def to_contract(ev: OfficeEvent) -> OfficeEventV1:
    return OfficeEventV1(
        seq=ev.seq,
        event_id=ev.event_id,
        type=ev.type,
        agent_id=ev.agent_id,
        task_id=ev.task_id,
        state=ev.state,
        timestamp=ev.occurred_at,
        mode=ev.mode,
        simulated=ev.mode == "DRY_RUN",
        payload=ev.payload,
    )


def events_after(db: Session, after_seq: int, limit: int = 500) -> list[OfficeEventV1]:
    rows = db.execute(
        select(OfficeEvent).where(OfficeEvent.seq > after_seq).order_by(OfficeEvent.seq).limit(limit)
    ).scalars()
    return [to_contract(r) for r in rows]


def snapshot(db: Session) -> PresenceSnapshotV1:
    # Uma única instrução SQL = um único snapshot MVCC: presença e as_of_seq são mutuamente consistentes.
    rows = db.execute(
        select(
            Agent,
            AgentPresence,
            select(func.coalesce(func.max(OfficeEvent.seq), 0)).scalar_subquery().label("as_of"),
            select(SystemFlag.value).where(SystemFlag.key == "emergency_stop").scalar_subquery().label("es"),
        )
        .join(AgentPresence, AgentPresence.agent_id == Agent.id)
        .order_by(Agent.id)
    ).all()
    s = get_settings()
    as_of = rows[0].as_of if rows else 0
    es = bool(rows[0].es and rows[0].es.get("active")) if rows else False
    agents = [
        PresenceV1(
            agent_id=a.id,
            name=a.name,
            role=a.role,
            room=a.room,
            color=a.color,
            appearance=a.appearance or {},
            state=p.state,
            task_id=p.task_id,
            last_seq=p.last_seq,
            last_event_at=p.updated_at,
        )
        for a, p, *_ in rows
    ]
    return PresenceSnapshotV1(
        as_of_seq=as_of, mode=s.mode.value, simulated=s.is_dry_run, emergency_stop=es, agents=agents
    )
