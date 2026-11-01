"""Missões & workflow no app desktop (UXD-010 / SDD v3 UX-04): lista, linha do tempo real, cancelamento e
reexecução como nova missão. Nada na tela é inventado: tudo deriva de agent_tasks, office_events e checkpoints."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from aicommerce.ai import runtime
from aicommerce.db import session_scope
from aicommerce.models import AgentTask, AuditEvent
from aicommerce.worker import run_once


def drain():
    while run_once() or runtime.advance_missions():
        pass


def start(client, h, goal="avaliar luminária de mesa", steps=3):
    r = client.post("/v1/missions", headers=h, json={"goal": goal, "max_steps": steps})
    assert r.status_code == 201, r.text
    return r.json()["mission_id"]


def test_list_and_detail_expose_progress_cost_and_real_timeline(client, auth):
    h = auth("operator")
    mid = start(client, h, "avaliar porta-temperos magnético")
    drain()
    items = client.get("/v1/missions", headers=h).json()["items"]
    row = next(i for i in items if i["id"] == mid)
    assert row["state"] == "COMPLETED" and row["blocker"] is None and row["simulated"] is True
    assert row["steps_total"] == 3 and row["steps_done"] == 3 and row["cost_micro_usd"] > 0
    assert set(row["agents"]) == {"atlas", "luna", "nexus", "finn"}
    assert row["goal"] == "avaliar porta-temperos magnético" and row["duration_s"] >= 0
    assert mid in [i["id"] for i in client.get("/v1/missions?state=DONE", headers=h).json()["items"]]
    assert mid not in [i["id"] for i in client.get("/v1/missions?state=OPEN", headers=h).json()["items"]]

    m = client.get(f"/v1/missions/{mid}", headers=h).json()
    assert all(c["why"] and c["created_at"] and c["cost_micro_usd"] >= 0 for c in m["children"])
    mission_states = [e["task_state"] for e in m["timeline"] if e["task_id"] == mid and e["task_state"]]
    for s in ("QUEUED", "WAITING_EXTERNAL", "RESUMED", "COMPLETED"):
        assert s in mission_states
    seqs = [e["seq"] for e in m["timeline"]]
    assert seqs == sorted(seqs)
    assert [c["node"] for c in m["checkpoints"] if c["task_id"] == mid] == [
        "context_built",
        "persisted",
        "children_spawned",
        "children_done",
        "summarized",
    ]


def test_cancel_stops_open_children_and_is_audited(client, auth):
    h = auth("operator")
    mid = start(client, h, "avaliar suporte de notebook")
    run_once()  # só o plano: fica WAITING_EXTERNAL com subtarefas na fila
    m = client.get(f"/v1/missions/{mid}", headers=h).json()
    assert m["state"] == "WAITING_EXTERNAL" and m["blocker"] == "WAITING_TOOL"
    assert mid in [i["id"] for i in client.get("/v1/missions?state=OPEN", headers=h).json()["items"]]

    assert client.post(f"/v1/missions/{mid}/cancel", headers=h, json={"reason": "x"}).status_code == 422
    r = client.post(f"/v1/missions/{mid}/cancel", headers=h, json={"reason": "objetivo mudou"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "CANCELLED" and all(c["state"] == "CANCELLED" for c in body["children"])
    drain()  # o worker não reabre nem conclui nada cancelado
    m = client.get(f"/v1/missions/{mid}", headers=h).json()
    assert m["state"] == "CANCELLED" and all(c["state"] == "CANCELLED" for c in m["children"])
    again = client.post(f"/v1/missions/{mid}/cancel", headers=h, json={"reason": "de novo"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "INVALID_STATE"
    with session_scope() as db:
        ev = db.execute(
            select(AuditEvent).where(AuditEvent.subject_id == mid, AuditEvent.action == "mission.cancelled")
        ).scalar_one()
        assert ev.payload["reason"] == "objetivo mudou" and len(ev.payload["children_cancelled"]) == 3


def test_task_cancelled_while_running_is_not_completed(client, auth):
    h = auth("operator")
    mid = start(client, h, "avaliar garrafa de vidro")
    run_once()
    with session_scope() as db:
        child = db.execute(select(AgentTask).where(AgentTask.parent_task_id == uuid.UUID(mid))).scalars().first()
        child.state = "RUNNING"  # simula o worker tendo reservado a subtarefa
        cid = child.id
    client.post(f"/v1/missions/{mid}/cancel", headers=h, json={"reason": "corrida com o worker"})
    assert runtime.run(cid) == "CANCELLED"
    with session_scope() as db:
        assert db.get(AgentTask, cid).state == "CANCELLED" and db.get(AgentTask, cid).result is None


def test_rerun_creates_new_mission_and_keeps_history(client, auth):
    h = auth("operator")
    mid = start(client, h, "avaliar organizador de gaveta", steps=2)
    r = client.post(f"/v1/missions/{mid}/rerun", headers=h)
    assert r.status_code == 409  # ainda em andamento
    drain()
    r = client.post(f"/v1/missions/{mid}/rerun", headers=h)
    assert r.status_code == 201, r.text
    new = r.json()["mission_id"]
    assert new != mid and r.json()["rerun_of"] == mid
    drain()
    n = client.get(f"/v1/missions/{new}", headers=h).json()
    assert n["state"] == "COMPLETED" and n["rerun_of"] == mid and n["goal"] == "avaliar organizador de gaveta"
    assert client.get(f"/v1/missions/{mid}", headers=h).json()["state"] == "COMPLETED"


def test_blocker_classification():
    from aicommerce.api.routes_ai import _blocker

    assert _blocker("FAILED", "LIMIT_REACHED: teto diário", []) == "BLOCKED_POLICY"
    assert _blocker("FAILED", "TRANSIENT: timeout", []) == "RECOVERABLE"
    assert _blocker("FAILED", "LEASE_EXPIRED: lease do worker expirou", []) == "RECOVERABLE"
    assert _blocker("FAILED", "algo inesperado", []) == "FAILED"
    assert _blocker("WAITING_EXTERNAL", None, ["COMPLETED", "WAITING_APPROVAL"]) == "WAITING_APPROVAL"
    assert _blocker("RUNNING", None, []) is None


def test_viewer_reads_but_cannot_cancel_or_rerun(client, auth):
    op, viewer = auth("operator"), auth("viewer")
    mid = start(client, op, "avaliar caneca térmica")
    assert client.get("/v1/missions", headers=viewer).status_code == 200
    assert client.get(f"/v1/missions/{mid}", headers=viewer).status_code == 200
    assert client.post(f"/v1/missions/{mid}/cancel", headers=viewer, json={"reason": "não pode"}).status_code == 403
    assert client.post(f"/v1/missions/{mid}/rerun", headers=viewer).status_code == 403
    assert client.get(f"/v1/missions/{uuid.uuid4()}", headers=viewer).status_code == 404
    drain()  # não deixa trabalho na fila para os testes seguintes


# ───────────── Base de conhecimento e auditoria no app (UXD-014) ─────────────
def test_knowledge_list_respects_acl_filters_and_flags_expiry(client, auth):
    from tests.test_ai import doc

    op, viewer, owner = auth("operator"), auth("viewer"), auth("owner")
    tag = uuid.uuid4().hex[:6]
    body = f"Documento {tag} de teste: conteúdo único para não deduplicar com outros testes da base."
    restricted = doc(
        client, op, title=f"Margens internas {tag}", access_policy=["finance"], collection="analytics", content=body
    )
    public = doc(
        client,
        op,
        title=f"Política de trocas {tag}",
        access_policy=["all"],
        valid_until="2020-01-01T00:00:00Z",
        content=body,
    )
    seen = {d["id"] for d in client.get(f"/v1/knowledge/documents?q={tag}", headers=viewer).json()["items"]}
    assert public["id"] in seen and restricted["id"] not in seen  # viewer não vê nem o título do restrito
    body = client.get(f"/v1/knowledge/documents?q={tag}", headers=owner).json()
    assert {public["id"], restricted["id"]} <= {d["id"] for d in body["items"]}  # revisor vê todos
    row = next(d for d in body["items"] if d["id"] == public["id"])
    assert row["expired"] is True and row["owner_email"] and "analytics" in body["collections"]
    only = client.get(f"/v1/knowledge/documents?q={tag}&collection=analytics", headers=owner).json()["items"]
    assert [d["id"] for d in only] == [restricted["id"]]


def test_audit_filters_and_pagination(client, auth):
    owner, op = auth("owner"), auth("operator")
    mid = start(client, op, "avaliar tapete de yoga")
    client.post(f"/v1/missions/{mid}/cancel", headers=op, json={"reason": "teste de auditoria"})
    assert client.get("/v1/audit/events", headers=op).status_code == 403
    rows = client.get("/v1/audit/events?action=mission.&limit=500", headers=owner).json()["items"]
    assert rows and all(r["action"].startswith("mission.") for r in rows)
    # grupo "Missões e tarefas" do app: vários prefixos (criação é agent_task.*, cancelamento é mission.*)
    both = client.get("/v1/audit/events?action=agent_task.,mission.&limit=500", headers=owner).json()["items"]
    kinds = {r["action"].split(".")[0] for r in both}
    assert kinds == {"agent_task", "mission"}
    assert any(r["subject_id"] == mid and r["action"].startswith("agent_task.") for r in both)
    ev = next(r for r in rows if r["subject_id"] == mid)
    assert ev["actor_label"].endswith("@test.local") or "@" in ev["actor_label"]
    page1 = client.get("/v1/audit/events?limit=3", headers=owner).json()
    assert len(page1["items"]) == 3 and page1["next_before_id"] == page1["items"][-1]["id"]
    page2 = client.get(f"/v1/audit/events?limit=3&before_id={page1['next_before_id']}", headers=owner).json()
    assert max(r["id"] for r in page2["items"]) < page1["next_before_id"]


def test_worker_crash_resumes_from_checkpoint_instead_of_failing(client, auth):
    """Auditoria A07: checkpoints são usados na retomada. Tarefa de IA que caiu antes de persistir volta à fila
    (uma vez); missão que caiu no resumo retoma de children_done sem recriar etapas."""
    import datetime as dt

    from aicommerce import worker
    from aicommerce.models import WorkflowCheckpoint
    from aicommerce.workforce import tasks as tasks_mod

    h = auth("operator")
    later = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10)
    # 1) tarefa de IA "presa" em RUNNING (worker morreu durante a chamada ao modelo)
    tid = client.post(
        "/v1/agent-tasks",
        headers=h,
        json={"agent_id": "luna", "kind": "research.opportunities", "input": {"topic": "garrafas"}},
    ).json()["id"]
    with session_scope() as db:
        t = db.get(AgentTask, uuid.UUID(tid))
        t.state, t.attempts = "RUNNING", 1
    with session_scope() as db:
        assert worker.expire_stale(db, later) >= 1
        assert db.get(AgentTask, uuid.UUID(tid)).state == "QUEUED"
    drain()
    assert client.get(f"/v1/agent-tasks/{tid}", headers=h).json()["state"] == "COMPLETED"
    # 2) segunda queda da mesma tarefa: não entra em laço — falha tipada
    tid2 = client.post(
        "/v1/agent-tasks",
        headers=h,
        json={"agent_id": "luna", "kind": "research.opportunities", "input": {"topic": "canecas"}},
    ).json()["id"]
    with session_scope() as db:
        t = db.get(AgentTask, uuid.UUID(tid2))
        t.state, t.attempts = "RUNNING", 2
    with session_scope() as db:
        worker.expire_stale(db, later)
        assert db.get(AgentTask, uuid.UUID(tid2)).state == "FAILED"
    # 3) missão que caiu durante o resumo final: retoma do checkpoint, etapas não são recriadas
    from aicommerce.ai import runtime as rt

    mid = start(client, h, "avaliar porta-copos de cortiça")
    while run_once():  # plano + etapas, sem o resumo (advance_missions não roda)
        pass
    with session_scope() as db:  # o que advance_missions faz antes de chamar o modelo para resumir… e o worker cai
        m = db.get(AgentTask, uuid.UUID(mid), with_for_update=True)
        tasks_mod.transition(db, m, tasks_mod.TaskState.RESUMED, detail={"node": "children_done"})
        rt.checkpoint(db, m.id, "children_done", {"states": []})
        tasks_mod.transition(db, m, tasks_mod.TaskState.RUNNING)
    kids_before = len(client.get(f"/v1/missions/{mid}", headers=h).json()["children"])
    with session_scope() as db:
        worker.expire_stale(db, later)
        assert db.get(AgentTask, uuid.UUID(mid)).state == "WAITING_EXTERNAL"
    drain()
    m = client.get(f"/v1/missions/{mid}", headers=h).json()
    assert m["state"] == "COMPLETED" and len(m["children"]) == kids_before
    with session_scope() as db:
        nodes = (
            db.execute(select(WorkflowCheckpoint.node).where(WorkflowCheckpoint.task_id == uuid.UUID(mid)))
            .scalars()
            .all()
        )
    assert "resumed_after_lease" in nodes


def test_every_agent_has_a_specialized_task_with_declared_tools():
    """Auditoria A08: 13 agentes, cada um com tarefa de IA própria; ferramentas declaradas pertencem ao agente."""
    from aicommerce.ai.specs import SPECS
    from aicommerce.workforce.registry import AGENTS

    tools_of = {a["id"]: set(a["tools"]) for a in AGENTS}
    assert {s.agent_id for s in SPECS.values()} == set(tools_of)  # os 13, incluindo a Nova
    for s in SPECS.values():
        for t in s.tools:
            assert t in tools_of[s.agent_id] or t in ("catalog.query", "orders.read"), (s.kind, t)


def test_tool_authorization_happens_before_any_query(client, auth, monkeypatch):
    from aicommerce.ai import specs

    h = auth("operator")
    called = []
    spec = specs.SPECS["channel.plan"]
    monkeypatch.setitem(
        specs.SPECS,
        "channel.plan",
        specs.Spec(**{**spec.__dict__, "tools": ("pagamentos.transferir",), "build": lambda db, inp: called.append(1)}),
    )
    tid = client.post(
        "/v1/agent-tasks", headers=h, json={"agent_id": "nova", "kind": "channel.plan", "input": {}}
    ).json()["id"]
    drain()
    t = client.get(f"/v1/agent-tasks/{tid}", headers=h).json()
    assert t["state"] == "FAILED" and "não permitida" in t["error"] and called == []  # nada consultado


def test_nova_channel_plan_runs_end_to_end(client, auth):
    h = auth("operator")
    tid = client.post(
        "/v1/agent-tasks", headers=h, json={"agent_id": "nova", "kind": "channel.plan", "input": {}}
    ).json()["id"]
    drain()
    t = client.get(f"/v1/agent-tasks/{tid}", headers=h).json()
    assert t["state"] == "COMPLETED", t
    plan = t["result"]["channel_plan"]
    assert {p["channel"] for p in plan} == {"WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"}
    assert all(
        p["fact_ids"] and p["action"] in ("MANTER", "PREPARAR", "CORRIGIR", "AGUARDAR_HOMOLOGACAO") for p in plan
    )
