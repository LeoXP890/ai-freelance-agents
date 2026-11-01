import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from aicommerce.db import session_scope
from aicommerce.finance import ledger
from aicommerce.governance import executor
from aicommerce.identity.security import issue_access_token
from aicommerce.models import ApprovalProposal, AuthorizationGrant, ExecutionJob
from aicommerce.providers.marketplaces import to_ml_item

# Propostas genéricas usam o canal Mercado Livre com payload no contrato do ML (ver test_marketplaces.py).
GO_LIVE = {
    "channel": "MERCADO_LIVRE",
    "supplier_origin": "NATIONAL",
    "fulfillment_mode": "DROPSHIP_SUPPLIER",
    "uf": "SP",
}


# Item no contrato do Mercado Livre (subconjunto verificado); o mock do ML rejeita payload fora dele.
ML_PAYLOAD = to_ml_item(
    title="Garrafa térmica 500ml",
    category_id="SIM-MLB1",
    price_minor=8990,
    stock_qty=5,
    images=[],
    sku="S1",
    brand=None,
    gtin=None,
)


def k():
    return f"k-{uuid.uuid4().hex}"


def submit(client, headers, **over):
    body = {
        "action_type": "PUBLISH_LISTING",
        "agent_id": "nova",
        "subject_type": "listing",
        "subject_id": f"L-{uuid.uuid4().hex[:6]}",
        "subject_version": 1,
        "payload": ML_PAYLOAD,
        "risk": "MEDIUM",
        "evidence_refs": ["dossier:1"],
        "idempotency_key": k(),
        "go_live": GO_LIVE,
    }
    body.update(over)
    r = client.post("/v1/proposals", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def decide(client, headers, prop, action="approve", **over):
    body = {
        "payload_sha256": prop["payload_sha256"],
        "subject_version": prop["subject_version"],
        "comment": "ok",
        "idempotency_key": k(),
    }
    body.update(over)
    return client.post(f"/v1/approvals/{prop['id']}/{action}", json=body, headers=headers)


def drain():
    while executor.execute_next() or executor.reconcile_next():
        pass


@pytest.fixture
def actors(auth):
    return auth("operator"), auth("owner")


def test_approve_creates_grant_and_job_then_executes_in_mock(client, actors):
    op, owner = actors
    prop = submit(client, op)
    assert prop["status"] == "PENDING_APPROVAL"
    r = decide(client, owner, prop)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "AUTHORIZED" and body["job"]["status"] == "QUEUED" and body["grant"]["consumed"] is False
    assert body["decisions"][0]["payload_sha256"] == prop["payload_sha256"]
    drain()
    final = client.get(f"/v1/approvals/{prop['id']}", headers=owner).json()
    assert final["status"] == "SUCCEEDED" and final["job"]["provider_ref"].startswith("SIM-mock-commerce-mercado_livre")
    assert final["grant"]["consumed"] is True and final["job"]["attempts"] == 1


def test_binding_to_exact_payload_and_version(client, actors):
    op, owner = actors
    prop = submit(client, op)
    r = decide(client, owner, prop, payload_sha256="0" * 64)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "STALE_PAYLOAD"
    r = decide(client, owner, prop, subject_version=2)
    assert r.json()["detail"]["code"] == "STALE_PAYLOAD"


def test_permissions_mfa_and_self_approval(client, actors, make_user):
    op, owner = actors
    prop = submit(client, op)
    assert decide(client, op, prop).status_code == 403  # operador não decide
    u = make_user("owner")
    no_mfa = {"Authorization": f"Bearer {issue_access_token(u['id'], mfa=False)}"}
    assert decide(client, no_mfa, prop).status_code == 403
    own = submit(client, owner)  # owner propôs → não pode aprovar a própria
    r = decide(client, owner, own)
    assert r.status_code == 403 and "própria" in r.json()["detail"]["message"]


def test_idempotent_decision_and_single_verdict(client, actors, auth):
    op, owner = actors
    prop = submit(client, op)
    key = k()
    a = decide(client, owner, prop, idempotency_key=key).json()
    b = decide(client, owner, prop, idempotency_key=key).json()
    assert b["replayed"] is True and a["job"]["id"] == b["job"]["id"]
    again = decide(client, auth("owner"), prop)  # outro decisor, outra chave
    assert again.status_code == 409 and again.json()["detail"]["code"] == "INVALID_STATE"
    with session_scope() as db:
        assert (
            db.execute(
                select(func.count()).select_from(ExecutionJob).where(ExecutionJob.proposal_id == uuid.UUID(prop["id"]))
            ).scalar_one()
            == 1
        )


def test_reject_and_request_changes_need_comment(client, actors):
    op, owner = actors
    p1, p2 = submit(client, op), submit(client, op)
    assert decide(client, owner, p1, "reject", comment="").status_code == 409
    r = decide(client, owner, p1, "reject", comment="preço fora da política")
    assert r.json()["status"] == "REJECTED" and r.json()["job"] is None
    r = decide(client, owner, p2, "request-changes", comment="faltam fotos")
    assert r.json()["status"] == "CHANGES_REQUESTED"


def test_decisions_are_append_only(db):
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE approval_decisions SET comment = 'x'"))


def test_expired_proposal_cannot_be_approved(client, actors):
    op, owner = actors
    prop = submit(client, op)
    with session_scope() as db:
        db.get(ApprovalProposal, uuid.UUID(prop["id"])).expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    r = decide(client, owner, prop)
    assert r.status_code == 409
    assert client.get(f"/v1/approvals/{prop['id']}", headers=owner).json()["status"] == "EXPIRED"


def test_new_version_supersedes_pending_and_authorized(client, actors):
    op, owner = actors
    v1 = submit(client, op, subject_id="L-SUP", subject_version=1)
    v1b = submit(client, op, subject_id="L-SUP2", subject_version=1)
    decide(client, owner, v1b)  # AUTHORIZED, job ainda na fila
    submit(client, op, subject_id="L-SUP", subject_version=2)
    submit(client, op, subject_id="L-SUP2", subject_version=2)
    s1 = client.get(f"/v1/approvals/{v1['id']}", headers=owner).json()
    s2 = client.get(f"/v1/approvals/{v1b['id']}", headers=owner).json()
    assert s1["status"] == "EXPIRED" and s2["status"] == "EXPIRED"
    assert s2["job"]["status"] == "CANCELLED" and s2["grant"]["revoked"] is True


def test_monetary_approval_reserves_budget_and_revoke_releases(client, actors, budget_account):
    op, owner = actors
    acc = budget_account(cap=10_000, hold=0)
    prop = submit(
        client,
        op,
        action_type="CREATE_CAMPAIGN",
        agent_id="pixel",
        subject_type="campaign",
        monetary_scope_minor=4_000,
        budget_account_id=str(acc),
        go_live=None,
    )
    body = decide(client, owner, prop).json()
    assert body["grant"]["reservation_id"]
    with session_scope() as db:
        assert ledger.budget_view(db, acc).available_minor == 6_000
    r = client.post(
        f"/v1/approvals/{prop['id']}/revoke",
        json={"reason": "mudou a estratégia", "idempotency_key": k()},
        headers=owner,
    )
    assert r.status_code == 200 and r.json()["status"] == "REVOKED"
    with session_scope() as db:
        assert ledger.budget_view(db, acc).available_minor == 10_000


def test_insufficient_budget_keeps_proposal_pending(client, actors, budget_account):
    op, owner = actors
    acc = budget_account(cap=1_000, hold=0)
    prop = submit(
        client,
        op,
        action_type="CREATE_CAMPAIGN",
        agent_id="pixel",
        subject_type="campaign",
        monetary_scope_minor=5_000,
        budget_account_id=str(acc),
        go_live=None,
    )
    r = decide(client, owner, prop)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "BUDGET_UNAVAILABLE"
    after = client.get(f"/v1/approvals/{prop['id']}", headers=owner).json()
    assert after["status"] == "PENDING_APPROVAL" and after["grant"] is None and after["job"] is None


def test_monetary_without_account_cannot_be_approved(client, actors):
    op, owner = actors
    prop = submit(
        client, op, action_type="CREATE_CAMPAIGN", subject_type="campaign", monetary_scope_minor=100, go_live=None
    )
    assert decide(client, owner, prop).json()["detail"]["code"] == "BUDGET_UNAVAILABLE"


def test_emergency_stop_blocks_approval(client, actors):
    op, owner = actors
    prop = submit(client, op)
    client.post("/v1/emergency-stop", json={"reason": "teste aprovação"}, headers=owner)
    try:
        r = decide(client, owner, prop)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "POLICY_CHANGED"
    finally:
        client.post("/v1/emergency-stop/release", json={"reason": "fim"}, headers=owner)


def test_capabilities_matrix(client, auth):
    r = client.get("/v1/channels/mercado_livre/capabilities", headers=auth("viewer")).json()
    assert r["simulated"] and r["capabilities"]["publish_listing"] == "SUPPORTED"
    assert (
        r["capabilities"]["fetch_orders"] == "SUPPORTED" and r["capabilities"]["contract_status"] == "VERIFIED_SUBSET"
    )
    tts = client.get("/v1/channels/TIKTOK_SHOP/capabilities", headers=auth("viewer")).json()["capabilities"]
    assert tts["cross_border_fulfillment"] == "BLOCKED_BY_POLICY" and tts["contract_status"] == "UNVERIFIED"
    assert tts["fetch_orders"] == "UNAVAILABLE"
    assert client.get("/v1/channels/amazon/capabilities", headers=auth("viewer")).status_code == 404


def test_grant_is_single_use(client, actors):
    op, owner = actors
    prop = submit(client, op)
    decide(client, owner, prop)
    drain()
    with session_scope() as db:
        grant = db.execute(
            select(AuthorizationGrant).where(AuthorizationGrant.proposal_id == uuid.UUID(prop["id"]))
        ).scalar_one()
        assert grant.consumed_at is not None
