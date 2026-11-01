"""Rotinas dos agentes: validação, agenda (Brasília), disparo pelo worker, sem acúmulo e respeitando o kill switch."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import update

from aicommerce.db import session_scope
from aicommerce.models import AgentRoutine, AgentTask
from aicommerce.workforce import routines

T0 = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)  # 09:00 em Brasília


@pytest.fixture(autouse=True)
def clean():
    yield
    with session_scope() as db:
        db.query(AgentRoutine).delete()


def test_api_creates_lists_and_validates(client, auth):
    op = auth("operator")
    r = client.post(
        "/v1/agent-routines",
        headers=op,
        json={
            "agent_id": "luna",
            "kind": "research.opportunities",
            "schedule": "DAILY",
            "time_of_day": "08:30",
            "text": "utilidades de cozinha",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["text"] == "utilidades de cozinha" and body["enabled"] and body["next_run_at"]
    lst = client.get("/v1/agent-routines?agent_id=luna", headers=op).json()
    assert [i["id"] for i in lst["items"]] == [body["id"]] and any(
        k["kind"] == "supplier.discover" for k in lst["kinds"]
    )
    bad = [
        {
            "agent_id": "nexus",
            "kind": "research.opportunities",
            "schedule": "DAILY",
            "time_of_day": "08:00",
            "text": "x y z",
        },
        {"agent_id": "luna", "kind": "ads.plan", "schedule": "DAILY", "time_of_day": "08:00"},
        {
            "agent_id": "luna",
            "kind": "research.opportunities",
            "schedule": "INTERVAL",
            "interval_hours": 1,
            "text": "abc",
        },
        {
            "agent_id": "luna",
            "kind": "research.opportunities",
            "schedule": "DAILY",
            "time_of_day": "25:00",
            "text": "abc",
        },
        {"agent_id": "luna", "kind": "research.opportunities", "schedule": "DAILY", "time_of_day": "08:00"},
    ]
    for b in bad:
        assert client.post("/v1/agent-routines", headers=op, json=b).status_code == 409, b
    off = client.patch(f"/v1/agent-routines/{body['id']}", headers=op, json={"enabled": False}).json()
    assert off["enabled"] is False and off["next_run_at"] is None
    assert client.delete(f"/v1/agent-routines/{body['id']}", headers=op).status_code == 204


def test_daily_schedule_uses_brasilia_time():
    r = AgentRoutine(schedule="DAILY", time_of_day="08:30")
    assert routines.next_run(r, T0) == dt.datetime(2026, 11, 2, 11, 30, tzinfo=dt.UTC)  # já passou hoje → amanhã
    r.time_of_day = "10:00"
    assert routines.next_run(r, T0) == dt.datetime(2026, 11, 1, 13, 0, tzinfo=dt.UTC)
    r = AgentRoutine(schedule="INTERVAL", interval_hours=6)
    assert routines.next_run(r, T0) == T0 + dt.timedelta(hours=6)


def test_worker_dispatches_due_routine_once_and_skips_while_running(monkeypatch):
    with session_scope() as db:
        r = routines.create(
            db, agent_id="finn", kind="finance.brief", schedule="INTERVAL", interval_hours=2, actor=None, now=T0
        )
        rid = r.id
    with session_scope() as db:
        assert routines.dispatch_due(db, now=T0 + dt.timedelta(hours=1)) == 0  # ainda não venceu
    with session_scope() as db:
        assert routines.dispatch_due(db, now=T0 + dt.timedelta(hours=2)) == 1
        r = db.get(AgentRoutine, rid)
        task = db.get(AgentTask, r.last_task_id)
        assert task.kind == "finance.brief" and task.input["routine_id"] == str(rid) and task.state == "QUEUED"
        assert r.next_run_at == T0 + dt.timedelta(hours=4)
    with session_scope() as db:  # tarefa anterior ainda na fila: pula sem acumular
        assert routines.dispatch_due(db, now=T0 + dt.timedelta(hours=4)) == 0
        assert db.get(AgentRoutine, rid).last_outcome == "PULADA_EM_ANDAMENTO"
        db.execute(update(AgentTask).where(AgentTask.id == task.id).values(state="COMPLETED"))
    with session_scope() as db:
        assert routines.dispatch_due(db, now=T0 + dt.timedelta(hours=6)) == 1


def test_emergency_stop_suspends_routines(monkeypatch):
    from aicommerce.governance import killswitch

    monkeypatch.setattr(killswitch, "is_active", lambda db: True)
    with session_scope() as db:
        r = routines.create(
            db, agent_id="orion", kind="analytics.report", schedule="INTERVAL", interval_hours=2, actor=None, now=T0
        )
        rid = r.id
    with session_scope() as db:
        assert routines.dispatch_due(db, now=T0 + dt.timedelta(hours=3)) == 0
        r = db.get(AgentRoutine, rid)
        assert r.last_outcome == "PULADA_EMERGENCIA" and r.last_task_id is None
        with pytest.raises(routines.RoutineError):
            routines.run_now(db, rid, actor=None)


def test_run_now_creates_task(client, auth):
    op = auth("operator")
    rid = client.post(
        "/v1/agent-routines",
        headers=op,
        json={
            "agent_id": "nexus",
            "kind": "supplier.discover",
            "schedule": "DAILY",
            "time_of_day": "07:00",
            "text": "formas de bolo",
        },
    ).json()["id"]
    r = client.post(f"/v1/agent-routines/{rid}/run", headers=op)
    assert r.status_code == 201 and r.json()["input"] == {"category": "formas de bolo", "routine_id": rid}
    assert client.post(f"/v1/agent-routines/{rid}/run", headers=op).status_code == 409  # anterior em andamento
    assert client.post(f"/v1/agent-routines/{uuid.uuid4()}/run", headers=op).status_code == 409
