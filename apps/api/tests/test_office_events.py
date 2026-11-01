import json
import pathlib

import jsonschema
import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from aicommerce import worker
from aicommerce.db import session_scope
from aicommerce.models import AgentTask
from aicommerce.office.contract import json_schemas
from aicommerce.workforce import tasks

CONTRACTS = pathlib.Path(__file__).resolve().parents[3] / "packages" / "contracts"


def drain_worker():
    while worker.run_once():
        pass


def test_contract_files_match_backend_models():
    """Drift do contrato: rode `uv run python ../../scripts/export_contracts.py` após mudar o schema."""
    for name, schema in json_schemas().items():
        assert json.loads((CONTRACTS / name).read_text()) == schema, name


def test_snapshot_has_13_agents_in_rooms(client, auth):
    snap = client.get("/v1/agent-presence/snapshot", headers=auth("viewer")).json()
    jsonschema.validate(snap, json_schemas()["presence-snapshot.v1.schema.json"])
    assert len(snap["agents"]) == 13 and snap["simulated"] is True and snap["mode"] == "DRY_RUN"
    assert {a["agent_id"] for a in snap["agents"]} >= {"atlas", "luna", "nexus", "finn", "clara", "vera"}
    agents = client.get("/v1/agents", headers=auth("viewer")).json()["items"]
    assert all(a["config_version"] == 1 and a["limits"]["max_handoffs"] == 3 for a in agents)
    assert not any("publish" in t or "spend" in t for a in agents for t in a["tools"])


def test_task_lifecycle_drives_presence_and_events(client, auth):
    h = auth("operator")
    snap = client.get("/v1/agent-presence/snapshot", headers=h).json()
    cursor = snap["as_of_seq"]
    r = client.post(
        "/v1/agent-tasks", json={"agent_id": "luna", "kind": "research.mock", "input": {"topic": "garrafas"}}, headers=h
    )
    assert r.status_code == 201 and r.json()["state"] == "QUEUED" and r.json()["simulated"] is True
    task_id = r.json()["id"]
    drain_worker()
    t = client.get(f"/v1/agent-tasks/{task_id}", headers=h).json()
    assert t["state"] == "COMPLETED" and t["result"]["simulated"] is True and t["result"]["sources"] == []

    evs = client.get(f"/v1/office/events?after_seq={cursor}", headers=h).json()["items"]
    mine = [e for e in evs if e["task_id"] == task_id]
    schema = json_schemas()["office-event.v1.schema.json"]
    for e in mine:
        jsonschema.validate(e, schema)
        assert e["simulated"] is True and e["mode"] == "DRY_RUN"
    assert [e["state"] for e in mine] == ["PLANNING", "PLANNING", "WORKING", "TOOL_CALL", "COMPLETED"]
    assert [e["type"] for e in mine][-1] == "agent.task.completed"
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    luna = next(
        a for a in client.get("/v1/agent-presence/snapshot", headers=h).json()["agents"] if a["agent_id"] == "luna"
    )
    assert luna["state"] == "COMPLETED" and luna["task_id"] == task_id and luna["last_seq"] == mine[-1]["seq"]


def test_unsupported_task_kind_fails_typed_never_fakes_success(client, auth):
    h = auth("operator")
    task_id = client.post(
        "/v1/agent-tasks", json={"agent_id": "nova", "kind": "listing.publish_real"}, headers=h
    ).json()["id"]
    drain_worker()
    t = client.get(f"/v1/agent-tasks/{task_id}", headers=h).json()
    assert t["state"] == "FAILED" and t["error"].startswith("UNSUPPORTED") and t["result"] is None


def test_unknown_agent_404(client, auth):
    assert (
        client.post("/v1/agent-tasks", json={"agent_id": "ghost", "kind": "x"}, headers=auth("operator")).status_code
        == 404
    )


def test_invalid_transition_rejected():
    with session_scope() as db:
        t = tasks.create(db, agent_id="finn", kind="foundation.heartbeat", input_={}, actor=None)
        with pytest.raises(tasks.InvalidTransition):
            tasks.transition(db, t, tasks.TaskState.COMPLETED)  # QUEUED → COMPLETED não existe
        tasks.transition(db, t, tasks.TaskState.CANCELLED)
        with pytest.raises(tasks.InvalidTransition):
            tasks.transition(db, t, tasks.TaskState.RUNNING)


def test_stale_running_task_expires_to_failed_for_reconciliation():
    import datetime as dt

    with session_scope() as db:
        t = tasks.create(db, agent_id="logan", kind="foundation.heartbeat", input_={}, actor=None)
        tasks.transition(db, t, tasks.TaskState.RUNNING)
        tid = t.id
    with session_scope() as db:
        assert worker.expire_stale(db, dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10)) >= 1
    with session_scope() as db:
        t = db.get(AgentTask, tid)
        assert t.state == "FAILED" and "reconciliar" in t.error


def test_websocket_replays_after_offset_then_streams(client, auth, make_user):
    from tests.conftest import login

    token = login(client, make_user("viewer"))
    h = {"Authorization": f"Bearer {token}"}
    cursor = client.get("/v1/agent-presence/snapshot", headers=h).json()["as_of_seq"]
    op = auth("operator")
    t1 = client.post("/v1/agent-tasks", json={"agent_id": "atlas", "kind": "foundation.heartbeat"}, headers=op).json()
    with client.websocket_connect("/v1/office/events") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token, "after_seq": cursor}))
        assert ws.receive_json()["type"] == "ready"
        replayed = [ws.receive_json()["event"] for _ in range(2)]  # created + queued de t1
        assert all(e["task_id"] == t1["id"] for e in replayed) and replayed[0]["seq"] > cursor
        t2 = client.post(
            "/v1/agent-tasks", json={"agent_id": "dante", "kind": "foundation.heartbeat"}, headers=op
        ).json()
        live = ws.receive_json()["event"]
        assert live["task_id"] == t2["id"] and live["seq"] > replayed[-1]["seq"]


def test_websocket_drops_revoked_session(client, auth, monkeypatch, make_user):
    """Auditoria A01: revogar a sessão (troca de senha/MFA/desativação) encerra o WebSocket já aberto."""
    from aicommerce.api import routes_office
    from aicommerce.db import session_scope
    from aicommerce.models import User
    from tests.conftest import login

    monkeypatch.setattr(routes_office, "WS_REVALIDATE_S", 0.0)
    u = make_user("operator")
    token = login(client, u)
    op = {"Authorization": f"Bearer {token}"}
    cursor = client.get("/v1/agent-presence/snapshot", headers=op).json()["as_of_seq"]
    with client.websocket_connect("/v1/office/events") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token, "after_seq": cursor}))
        assert ws.receive_json()["type"] == "ready"
        with session_scope() as db:
            user = db.execute(select(User).where(User.email == u["email"])).scalar_one()
            user.session_version += 1
        client.post(
            "/v1/agent-tasks", json={"agent_id": "dante", "kind": "foundation.heartbeat"}, headers=auth("operator")
        )
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:  # nenhum evento novo pode chegar depois da revogação
                msg = ws.receive_json()
                assert msg["type"] != "event", "evento entregue a sessão revogada"
        assert exc.value.code == 4401


def test_websocket_rejects_missing_or_bad_token(client):
    with client.websocket_connect("/v1/office/events") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "bad"}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_office_events_are_append_only(db):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE office_events SET state = 'IDLE'"))
