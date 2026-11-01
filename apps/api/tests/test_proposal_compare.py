"""Dossiê mostra o que a proposta MUDA em relação ao que já foi executado para o mesmo assunto (UXD-011)."""

from __future__ import annotations

import uuid

from sqlalchemy import update

from aicommerce.db import session_scope
from aicommerce.models import ApprovalProposal
from tests.test_proposals import proposal


def submit(client, h, subject, payload, action="PUBLISH_LISTING"):
    r = client.post("/v1/proposals", json=proposal(subject_id=subject, payload=payload, action_type=action), headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def detail(client, h, pid):
    r = client.get(f"/v1/approvals/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["comparison"]


def test_first_proposal_has_nothing_to_compare(client, auth):
    h = auth("operator")
    assert detail(client, h, submit(client, h, f"L-{uuid.uuid4().hex[:6]}", {"title": "A"})) is None


def test_update_is_compared_with_the_executed_version_field_by_field(client, auth):
    h = auth("operator")
    subject = f"L-{uuid.uuid4().hex[:6]}"
    published = {"title": "Garrafa", "price": 89.9, "pictures": [{"source": "a.jpg"}], "_internal": 1}
    first = submit(client, h, subject, published)
    with session_scope() as db:
        db.execute(update(ApprovalProposal).where(ApprovalProposal.id == first).values(status="SUCCEEDED"))
    draft = submit(client, h, subject, {**published, "title": "Garrafa", "price": 79.9})  # proposta intermediária
    changed = {**published, "price": 74.9, "pictures": [{"source": "b.jpg"}], "_internal": 2}
    pid = submit(client, h, subject, changed, action="UPDATE_LISTING_MATERIAL")
    c = detail(client, h, pid)
    assert c["base"]["id"] == first and c["base"]["executed"] is True  # base = o que está no ar, não o rascunho
    assert {x["path"]: (x["before"], x["after"]) for x in c["changes"]} == {
        "price": (89.9, 74.9),
        "pictures[0].source": ("a.jpg", "b.jpg"),
    }  # campos internos (_…) ficam de fora
    assert detail(client, h, draft)["base"]["id"] == first


def test_without_execution_compares_with_previous_proposal_and_ignores_other_actions(client, auth):
    h = auth("operator")
    subject = f"L-{uuid.uuid4().hex[:6]}"
    first = submit(client, h, subject, {"title": "A", "price": 10})
    submit(client, h, subject, {"channel": "WOOCOMMERCE", "external_id": "9", "reason": "x"}, action="PAUSE_LISTING")
    pid = submit(client, h, subject, {"title": "B", "price": 10})
    c = detail(client, h, pid)
    assert c["base"]["id"] == first and c["base"]["executed"] is False  # pausa não é comparável com material
    assert c["changes"] == [{"path": "title", "before": "A", "after": "B"}]
