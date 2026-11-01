import datetime as dt
import threading
import uuid

from sqlalchemy import func, select

from aicommerce.db import session_scope
from aicommerce.finance import ledger
from aicommerce.governance import executor
from aicommerce.models import ExecutionJob, ExternalActionAttempt, SimProviderEffect
from tests.test_approvals import ML_PAYLOAD, decide, k, submit

FUTURE = dt.timedelta(minutes=10)


def approved(client, op, owner, fault=None, **over):
    payload = {**ML_PAYLOAD, **({"_simulate": fault} if fault else {})}
    prop = submit(client, op, payload=payload, **over)
    r = decide(client, owner, prop)
    assert r.status_code == 200, r.text
    return prop


def state(client, owner, prop):
    return client.get(f"/v1/approvals/{prop['id']}", headers=owner).json()


def effects(prop_id):
    with session_scope() as db:
        return db.execute(
            select(func.count())
            .select_from(SimProviderEffect)
            .where(SimProviderEffect.idempotency_key == f"exec:{prop_id}")
        ).scalar_one()


def run_until_idle(now=None):
    while executor.execute_next(now) or executor.reconcile_next(now):
        pass


def later():
    return dt.datetime.now(dt.UTC) + FUTURE


def test_timeout_after_effect_reconciles_without_duplicate(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner, "timeout_after")
    assert executor.execute_next()
    s = state(client, owner, prop)
    assert s["status"] == "UNKNOWN" and s["job"]["status"] == "UNKNOWN"
    assert effects(prop["id"]) == 1  # o efeito ocorreu apesar do timeout
    assert executor.reconcile_next(later())
    s = state(client, owner, prop)
    assert s["status"] == "SUCCEEDED" and s["job"]["attempts"] == 1  # nenhuma reexecução
    assert effects(prop["id"]) == 1


def test_timeout_before_effect_retries_with_same_key(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner, "timeout_before")
    executor.execute_next()
    assert effects(prop["id"]) == 0
    executor.reconcile_next(later())  # provedor confirma ausência → retry seguro
    run_until_idle(later())
    s = state(client, owner, prop)
    assert s["status"] == "SUCCEEDED" and s["job"]["attempts"] == 2 and effects(prop["id"]) == 1


def test_transient_failure_retries_with_backoff(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner, "transient_once")
    executor.execute_next()
    s = state(client, owner, prop)
    assert s["job"]["status"] == "RETRY" and s["status"] == "AUTHORIZED"
    assert not executor.execute_next()  # backoff: ainda não é hora
    run_until_idle(later())
    assert state(client, owner, prop)["status"] == "SUCCEEDED"


def test_permanent_rejection_fails_and_releases_budget(client, auth, budget_account):
    op, owner = auth("operator"), auth("owner")
    acc = budget_account(cap=5_000, hold=0)
    prop = approved(
        client,
        op,
        owner,
        "reject",
        action_type="CREATE_CAMPAIGN",
        agent_id="pixel",
        subject_type="campaign",
        monetary_scope_minor=2_000,
        budget_account_id=str(acc),
        go_live=None,
    )
    with session_scope() as db:
        assert ledger.budget_view(db, acc).available_minor == 3_000
    run_until_idle()
    s = state(client, owner, prop)
    assert s["status"] == "FAILED" and s["job"]["error_kind"] == "REJECTED" and s["job"]["attempts"] == 1
    with session_scope() as db:
        assert ledger.budget_view(db, acc).available_minor == 5_000


def test_persistent_transient_exhausts_attempts(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner, "fail")
    for _ in range(5):
        run_until_idle(dt.datetime.now(dt.UTC) + dt.timedelta(hours=1))
    s = state(client, owner, prop)
    assert s["status"] == "FAILED" and s["job"]["attempts"] == 3 and s["job"]["error_kind"] == "TRANSIENT"


def test_unsupported_action_fails_typed(client, auth, budget_account):
    op, owner = auth("operator"), auth("owner")
    acc = budget_account()
    prop = approved(
        client,
        op,
        owner,
        action_type="CHANGE_SUPPLIER",
        agent_id="nexus",
        subject_type="supplier",
        budget_account_id=str(acc),
        go_live=None,
    )
    run_until_idle()
    s = state(client, owner, prop)
    assert s["status"] == "FAILED" and s["job"]["error_kind"] == "UNSUPPORTED" and s["job"]["provider_ref"] is None


def test_kill_switch_freezes_queue_then_policy_denies(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner)
    client.post("/v1/emergency-stop", json={"reason": "teste executor"}, headers=owner)
    try:
        # só as pausas pedidas pelo próprio stop saem da fila (GOV-005); o resto fica congelado
        ran = []
        while True:
            with session_scope() as db:
                nxt = db.execute(
                    select(ExecutionJob.action_type)
                    .where(ExecutionJob.status.in_(("QUEUED", "RETRY")), ExecutionJob.action_type.like("PAUSE_%"))
                    .limit(1)
                ).scalar()
            if nxt is None or not executor.execute_next():
                break
            ran.append(nxt)
        assert all(a.startswith("PAUSE_") for a in ran)
        assert not executor.execute_next()  # fila congelada: nada além das pausas sai
        assert state(client, owner, prop)["job"]["status"] == "QUEUED"
    finally:
        client.post("/v1/emergency-stop/release", json={"reason": "fim"}, headers=owner)
    run_until_idle()
    assert state(client, owner, prop)["status"] == "SUCCEEDED"


def test_deadline_exceeded(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner)
    executor.execute_next(dt.datetime.now(dt.UTC) + dt.timedelta(days=2))
    s = state(client, owner, prop)
    assert s["status"] == "FAILED" and s["job"]["error_kind"] == "DEADLINE_EXCEEDED"


def test_crashed_worker_lease_is_reconciled(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner)
    with session_scope() as db:  # simula worker que morreu após T1 (EXECUTING) e antes da chamada
        job = db.execute(select(ExecutionJob).where(ExecutionJob.proposal_id == uuid.UUID(prop["id"]))).scalar_one()
        job.status, job.attempts = "EXECUTING", 1
        job.lease_until = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    assert executor.reconcile_next()
    run_until_idle(later())
    s = state(client, owner, prop)
    assert s["status"] == "SUCCEEDED" and effects(prop["id"]) == 1


def test_concurrent_workers_execute_once(client, auth):
    op, owner = auth("operator"), auth("owner")
    props = [approved(client, op, owner) for _ in range(6)]
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        run_until_idle()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for p in props:
        s = state(client, owner, p)
        assert s["status"] == "SUCCEEDED" and s["job"]["attempts"] == 1 and effects(p["id"]) == 1
        with session_scope() as db:
            n = db.execute(
                select(func.count())
                .select_from(ExternalActionAttempt)
                .where(ExternalActionAttempt.job_id == uuid.UUID(s["job"]["id"]))
            ).scalar_one()
            assert n == 1


def test_auto_authorized_pause_goes_through_executor(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = submit(client, op, action_type="PAUSE_LISTING", risk="LOW")
    assert prop["status"] == "AUTHORIZED"  # política autoriza pausa; mesmo pipeline
    run_until_idle()
    assert state(client, owner, prop)["status"] == "SUCCEEDED"


def test_execution_events_reach_office(client, auth):
    op, owner = auth("operator"), auth("owner")
    cursor = client.get("/v1/agent-presence/snapshot", headers=owner).json()["as_of_seq"]
    prop = approved(client, op, owner)
    run_until_idle()
    evs = [
        e
        for e in client.get(f"/v1/office/events?after_seq={cursor}", headers=owner).json()["items"]
        if e["payload"].get("proposal_id") == prop["id"]
    ]
    assert [e["type"] for e in evs] == [
        "approval.requested",
        "approval.decided",
        "listing.publish.requested",
        "listing.publish.confirmed",
    ]
    assert [e["state"] for e in evs] == ["WAITING_APPROVAL", "PLANNING", "TOOL_CALL", "COMPLETED"]
    assert evs[-1]["payload"]["simulated"] is True


def test_revoke_after_execution_is_refused(client, auth):
    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner)
    run_until_idle()
    r = client.post(
        f"/v1/approvals/{prop['id']}/revoke", json={"reason": "tarde demais", "idempotency_key": k()}, headers=owner
    )
    assert r.status_code == 409


def test_concurrent_reconcilers_respect_the_lease(client, auth, monkeypatch):
    """Auditoria A04: dois reconciliadores ao mesmo tempo — só um consulta o provedor e conclui o job."""
    from aicommerce.providers import marketplaces

    op, owner = auth("operator"), auth("owner")
    prop = approved(client, op, owner, "timeout_after")
    assert executor.execute_next()
    calls, barrier = [], threading.Barrier(2)
    original = marketplaces.MockMarketplaceProvider.lookup

    def slow_lookup(self, key, payload=None):
        calls.append(key)
        import time

        time.sleep(0.4)
        return original(self, key, payload)

    monkeypatch.setattr(marketplaces.MockMarketplaceProvider, "lookup", slow_lookup)
    at = later()

    def worker():
        barrier.wait()
        executor.reconcile_next(at)

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls.count(f"exec:{prop['id']}") == 1
    s = state(client, owner, prop)
    assert s["status"] == "SUCCEEDED" and effects(prop["id"]) == 1
