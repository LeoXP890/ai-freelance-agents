import base64
import uuid

import pytest

from aicommerce.api.routes_governance import ProposalIn
from aicommerce.config import RuntimeMode, Settings
from aicommerce.db import session_scope
from aicommerce.governance import golive, service

LIVE_PII_KEY = base64.b64encode(b"k" * 32).decode()

GO_LIVE = {
    "channel": "MERCADO_LIVRE",
    "supplier_origin": "NATIONAL",
    "fulfillment_mode": "DROPSHIP_SUPPLIER",
    "uf": "SP",
}


def proposal(**over):
    body = {
        "action_type": "PUBLISH_LISTING",
        "agent_id": "nova",
        "subject_type": "listing",
        "subject_id": "L-1",
        "subject_version": 4,
        "payload": {"title": "Produto X", "price_minor": 9990},
        "risk": "MEDIUM",
        "evidence_refs": ["dossier:1"],
        "idempotency_key": f"p-{uuid.uuid4().hex}",
        "go_live": GO_LIVE,
    }
    body.update(over)
    return body


def test_publish_in_dry_run_goes_to_human_approval_and_notes_live_denial(client, auth):
    r = client.post("/v1/proposals", json=proposal(), headers=auth("operator"))
    assert r.status_code == 201
    b = r.json()
    assert b["status"] == "PENDING_APPROVAL" and b["simulated"] is True and b["policy_version"] == "2026.10.3"
    assert any("equivalente LIVE seria DENY" in x for x in b["policy_reasons"])
    assert len(b["payload_sha256"]) == 64


def test_idempotent_submit_and_conflict(client, auth):
    h = auth("operator")
    body = proposal()
    first = client.post("/v1/proposals", json=body, headers=h).json()
    again = client.post("/v1/proposals", json=body, headers=h).json()
    assert again["id"] == first["id"] and again["replayed"] is True
    changed = {**body, "payload": {"title": "outro"}}
    assert client.post("/v1/proposals", json=changed, headers=h).status_code == 409


def test_unknown_action_and_missing_evidence(client, auth):
    h = auth("operator")
    assert client.post("/v1/proposals", json=proposal(action_type="WIRE_MONEY"), headers=h).json()["status"] == "DENIED"
    assert (
        client.post("/v1/proposals", json=proposal(evidence_refs=[]), headers=h).json()["status"] == "CHANGES_REQUESTED"
    )


def test_viewer_cannot_submit_and_list_filters(client, auth):
    assert client.post("/v1/proposals", json=proposal(), headers=auth("viewer")).status_code == 403
    client.post("/v1/proposals", json=proposal(), headers=auth("operator"))
    items = client.get("/v1/approvals?status=PENDING_APPROVAL", headers=auth("viewer")).json()["items"]
    assert items and all(i["status"] == "PENDING_APPROVAL" for i in items)


def test_live_mode_is_denied_while_gates_pending(monkeypatch):
    """A API deve recusar chamadas LIVE se qualquer gate aplicável estiver pendente (SDD §23.1)."""
    live = Settings(
        mode=RuntimeMode.LIVE,
        jwt_secret="l" * 40,
        woo_webhook_secret="w" * 40,
        pii_key=LIVE_PII_KEY,
        metrics_token="m" * 32,
    )
    monkeypatch.setattr(service, "get_settings", lambda: live)
    req = golive.GoLiveRequest(
        golive.Channel.WOOCOMMERCE, golive.SupplierOrigin.NATIONAL, golive.FulfillmentMode.DROPSHIP_SUPPLIER, "SP"
    )
    with session_scope() as db:
        prop, _ = service.submit_proposal(
            db, ProposalIn(**proposal()).model_dump(exclude={"go_live"}), actor="test", request_id=None, golive_req=req
        )
        assert prop.status == "DENIED" and prop.mode == "LIVE"
        assert "LIVE sem todos os gates" in prop.policy_reasons[0]
        db.rollback()


@pytest.mark.parametrize("action", ["PUBLISH_LISTING", "SPEND", "CREATE_CAMPAIGN", "REFUND", "PAUSE_MEDIA"])
def test_live_without_channel_context_is_denied(monkeypatch, action):
    live = Settings(
        mode=RuntimeMode.LIVE,
        jwt_secret="l" * 40,
        woo_webhook_secret="w" * 40,
        pii_key=LIVE_PII_KEY,
        metrics_token="m" * 32,
    )
    monkeypatch.setattr(service, "get_settings", lambda: live)
    with session_scope() as db:
        data = ProposalIn(**proposal(action_type=action, monetary_scope_minor=100)).model_dump(exclude={"go_live"})
        prop, _ = service.submit_proposal(db, data, actor="test", request_id=None, golive_req=None)
        assert prop.status == "DENIED"
        db.rollback()
