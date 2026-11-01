from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from aicommerce.api.deps import principal_from_token, require
from aicommerce.config import get_settings
from aicommerce.db import get_db, session_scope
from aicommerce.identity.security import Principal
from aicommerce.models import Agent, AgentConfig, AgentTask
from aicommerce.office import events
from aicommerce.workforce import tasks

router = APIRouter(prefix="/v1", tags=["office"])

WS_AUTH_TIMEOUT_S = 5
WS_HEARTBEAT_S = 15
WS_REVALIDATE_S = 5.0  # sessão revogada (senha/MFA/desativação/dispositivo) ou token vencido derruba a conexão


@router.get("/agents")
def list_agents(db: Session = Depends(get_db), _: Principal = Depends(require("office:read"))):
    out = []
    for a in db.execute(select(Agent).order_by(Agent.id)).scalars():
        cfg = (
            db.execute(select(AgentConfig).where(AgentConfig.agent_id == a.id).order_by(AgentConfig.version.desc()))
            .scalars()
            .first()
        )
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "role": a.role,
                "room": a.room,
                "color": a.color,
                "config_version": cfg.version if cfg else None,
                "tools": cfg.tools if cfg else [],
                "limits": cfg.limits if cfg else {},
            }
        )
    return {"items": out}


@router.get("/agent-presence/snapshot")
def presence_snapshot(db: Session = Depends(get_db), _: Principal = Depends(require("office:read"))):
    return events.snapshot(db).model_dump(mode="json")


@router.get("/office/events")
def office_events_http(
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("office:read")),
):
    """Fallback HTTP (modo 2D / redes sem WebSocket): mesma semântica de offset do WS."""
    items = events.events_after(db, after_seq, limit)
    return {
        "items": [e.model_dump(mode="json") for e in items],
        "next_after_seq": items[-1].seq if items else after_seq,
    }


def _authorize_ws(token: str) -> Principal:
    with session_scope() as db:
        p = principal_from_token(db, token)
    if not p.has("office:read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "office:read")
    return p


def _fetch(after_seq: int) -> list[dict[str, Any]]:
    with session_scope() as db:
        return [e.model_dump(mode="json") for e in events.events_after(db, after_seq, 500)]


@router.websocket("/office/events")
async def office_events_ws(ws: WebSocket):
    """Protocolo: cliente envia {"type":"auth","token":..., "after_seq": N} em até 5 s.
    Servidor reenvia eventos com seq > N (replay) e depois transmite novos eventos em ordem."""
    await ws.accept()
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), WS_AUTH_TIMEOUT_S))
        if hello.get("type") != "auth":
            raise ValueError
        token = str(hello.get("token", ""))
        await run_in_threadpool(_authorize_ws, token)
        cursor = max(int(hello.get("after_seq", 0)), 0)
    except (TimeoutError, ValueError, TypeError, HTTPException, json.JSONDecodeError):
        await ws.close(code=4401, reason="unauthorized")
        return
    await ws.send_json({"type": "ready", "after_seq": cursor, "mode": get_settings().mode.value})
    poll = get_settings().office_poll_interval_ms / 1000
    last_beat = last_auth = time.monotonic()
    try:
        while True:
            batch = await run_in_threadpool(_fetch, cursor)
            if time.monotonic() - last_auth >= WS_REVALIDATE_S:
                # antes de ENVIAR: mesma validação do HTTP (expiração, versão da sessão, usuário ativo, permissão)
                try:
                    await run_in_threadpool(_authorize_ws, token)
                except HTTPException:
                    await ws.close(code=4401, reason="session revoked")
                    return
                last_auth = time.monotonic()
            for ev in batch:
                await ws.send_json({"type": "event", "event": ev})
                cursor = ev["seq"]
            if time.monotonic() - last_beat > WS_HEARTBEAT_S:
                await ws.send_json({"type": "heartbeat", "cursor": cursor})
                last_beat = time.monotonic()
            if not batch:
                await asyncio.sleep(poll)
    except (WebSocketDisconnect, RuntimeError):
        return


class TaskIn(BaseModel):
    agent_id: str = Field(max_length=32)
    kind: str = Field(max_length=64)
    input: dict[str, Any] = Field(default_factory=dict)


def task_out(t: AgentTask) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "agent_id": t.agent_id,
        "kind": t.kind,
        "state": t.state,
        "mode": t.mode,
        "simulated": t.mode == "DRY_RUN",
        "input": t.input,
        "result": t.result,
        "error": t.error,
        "attempts": t.attempts,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.post("/agent-tasks", status_code=status.HTTP_201_CREATED)
def create_task(body: TaskIn, db: Session = Depends(get_db), p: Principal = Depends(require("tasks:create"))):
    try:
        t = tasks.create(db, agent_id=body.agent_id, kind=body.kind, input_=body.input, actor=p.user_id)
    except tasks.UnknownAgent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agente desconhecido") from None
    return task_out(t)


@router.get("/agent-tasks/{task_id}")
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("tasks:read"))):
    t = db.get(AgentTask, task_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tarefa inexistente")
    return task_out(t)


@router.get("/agent-tasks")
def list_tasks(
    agent_id: str | None = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("tasks:read")),
):
    q = select(AgentTask).order_by(AgentTask.created_at.desc()).limit(limit)
    if agent_id:
        q = q.where(AgentTask.agent_id == agent_id)
    return {"items": [task_out(t) for t in db.execute(q).scalars()]}


# ───────────── Rotinas dos agentes (tarefas recorrentes) ─────────────
class RoutineIn(BaseModel):
    agent_id: str = Field(max_length=32)
    kind: str = Field(max_length=64)
    schedule: Literal["DAILY", "INTERVAL"]
    time_of_day: str | None = Field(default=None, max_length=5)
    interval_hours: int | None = None
    text: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=120)


class RoutinePatch(BaseModel):
    enabled: bool | None = None
    schedule: Literal["DAILY", "INTERVAL"] | None = None
    time_of_day: str | None = Field(default=None, max_length=5)
    interval_hours: int | None = None
    text: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=120)


def _routine_call(fn):
    from aicommerce.workforce import routines

    try:
        return fn()
    except routines.RoutineError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/agent-routines")
def list_routines(
    agent_id: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(require("tasks:read"))
):
    from aicommerce.models import AgentRoutine
    from aicommerce.workforce import routines

    q = select(AgentRoutine).order_by(AgentRoutine.created_at)
    if agent_id:
        q = q.where(AgentRoutine.agent_id == agent_id)
    return {"items": [routines.out(r) for r in db.execute(q).scalars()], "kinds": routines.catalog()}


@router.post("/agent-routines", status_code=status.HTTP_201_CREATED)
def create_routine(body: RoutineIn, db: Session = Depends(get_db), p: Principal = Depends(require("tasks:create"))):
    from aicommerce.workforce import routines

    return routines.out(_routine_call(lambda: routines.create(db, **body.model_dump(), actor=p.user_id)))


@router.patch("/agent-routines/{routine_id}")
def update_routine(
    routine_id: uuid.UUID,
    body: RoutinePatch,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("tasks:create")),
):
    from aicommerce.workforce import routines

    fields = body.model_dump(exclude_unset=True)
    return routines.out(_routine_call(lambda: routines.update(db, routine_id, actor=p.user_id, **fields)))


@router.delete("/agent-routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routine(
    routine_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("tasks:create"))
):
    from aicommerce.workforce import routines

    _routine_call(lambda: routines.delete(db, routine_id, actor=p.user_id))


@router.post("/agent-routines/{routine_id}/run", status_code=status.HTTP_201_CREATED)
def run_routine(routine_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("tasks:create"))):
    from aicommerce.workforce import routines

    return task_out(_routine_call(lambda: routines.run_now(db, routine_id, actor=p.user_id)))


# ───────────── Pool de ferramentas dos agentes ─────────────
@router.get("/agent-tools")
def list_agent_tools(
    agent_id: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(require("tasks:read"))
):
    """Catálogo de ferramentas, quem pode usar e o uso nos últimos 30 dias (chamadas, cache e custo)."""
    import datetime as dt

    from sqlalchemy import func

    from aicommerce.ai import tools
    from aicommerce.models import ToolCall
    from aicommerce.workforce.registry import AGENTS

    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    q = (
        select(ToolCall.tool, func.count(), func.count().filter(ToolCall.cache_hit), func.sum(ToolCall.cost_micro_usd))
        .where(ToolCall.created_at >= since, ToolCall.status == "OK")
        .group_by(ToolCall.tool)
    )
    if agent_id:
        q = q.where(ToolCall.agent_id == agent_id)
    usage = {t: {"calls": n, "cache_hits": h, "cost_micro_usd": int(c or 0)} for t, n, h, c in db.execute(q)}
    items = []
    for t in tools.catalog():
        agents = [a["id"] for a in AGENTS if t["always_allowed"] or t["name"] in a["tools"]]
        if agent_id and agent_id not in agents:
            continue
        items.append(
            {
                **t,
                "agents": agents,
                "usage_30d": usage.get(t["name"], {"calls": 0, "cache_hits": 0, "cost_micro_usd": 0}),
            }
        )
    return {"items": items}


ACCESSORIES = ("none", "hat", "glasses", "headset", "cap")


class AppearanceIn(BaseModel):
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    accessory: str = Field(default="none")
    skin: str = Field(default="#f1e3d3", pattern=r"^#[0-9a-fA-F]{6}$")


@router.put("/agents/{agent_id}/appearance")
def set_appearance(
    agent_id: str, body: AppearanceIn, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))
):
    """Aparência do avatar (OFF-002). Puramente visual: não altera ferramentas, limites nem permissões."""
    from aicommerce.governance import audit

    if body.accessory not in ACCESSORIES:
        raise HTTPException(422, f"acessório inválido; use {list(ACCESSORIES)}")
    a = db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agente desconhecido")
    before = {"color": a.color, **(a.appearance or {})}
    a.color, a.appearance = body.color.lower(), {"accessory": body.accessory, "skin": body.skin.lower()}
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="agent.appearance.changed",
        subject_type="agent",
        subject_id=agent_id,
        payload={"before": before, "after": body.model_dump()},
    )
    return {"id": a.id, "color": a.color, "appearance": a.appearance}
