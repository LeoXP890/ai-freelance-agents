import pytest
from sqlalchemy import select, text

from aicommerce.db import session_scope
from aicommerce.finance import ledger
from aicommerce.governance import audit


def test_audit_chain_intact_and_verifiable_via_api(client, auth):
    h = auth("owner")
    client.post("/v1/auth/login", json={"email": "nao@existe.local", "password": "x"})
    r = client.get("/v1/audit/verify", headers=h)
    assert r.status_code == 200 and r.json() == {"intact": True, "first_invalid_id": None}
    assert client.get("/v1/audit/verify", headers=auth("operator")).status_code == 403


def test_tampering_is_detected(db):
    audit.record(
        db, actor_type="system", actor_id="t", action="test.tamper", subject_type="t", subject_id="t", payload={"v": 1}
    )
    db.flush()
    target = db.execute(select(audit.AuditEvent.id).order_by(audit.AuditEvent.id.desc()).limit(1)).scalar()
    # Simula adulteração por quem tem privilégio de dono da tabela (trigger desativado nesta transação).
    db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete"))
    db.execute(text("UPDATE audit_events SET payload = '{\"v\": 2}' WHERE id = :i"), {"i": target})
    ok, bad = audit.verify_chain(db)
    assert ok is False and bad == target
    db.rollback()
    ok, _ = audit.verify_chain(db)
    assert ok is True


def test_secrets_are_redacted():
    assert audit.redact({"password": "x", "nested": [{"api_key": "y", "ok": 1}]}) == {
        "password": "[REDACTED]",
        "nested": [{"api_key": "[REDACTED]", "ok": 1}],
    }


def test_emergency_stop_blocks_new_spend_but_reconciles_existing(client, auth, budget_account):
    acc = budget_account(cap=10_000, hold=0)
    with session_scope() as s:
        resv, _ = ledger.reserve(
            s,
            acc,
            amount_minor=1_000,
            currency="BRL",
            idempotency_key="es-pre-1-xyz",
            purpose="antes do stop",
            actor="test",
        )
        resv_id = resv.id
    owner = auth("owner")
    r = client.post("/v1/emergency-stop", json={"reason": "teste de kill switch"}, headers=owner)
    try:
        assert r.status_code == 200 and r.json()["active"] is True and r.json()["queues_frozen"] is True
        assert r.json()["external_pause_requests"] == []
        assert client.post("/v1/emergency-stop", json={"reason": "de novo"}, headers=owner).json()["replayed"]
        with pytest.raises(ledger.SpendingFrozen), session_scope() as s:
            ledger.reserve(
                s, acc, amount_minor=1, currency="BRL", idempotency_key="es-new-1-xyz", purpose="x", actor="test"
            )
        with session_scope() as s:  # gasto já ocorrido continua sendo assimilado
            out = ledger.record_spend(
                s,
                acc,
                amount_minor=500,
                currency="BRL",
                external_ref="ads-1",
                idempotency_key="es-spend-1-xyz",
                actor="test",
                reservation_id=resv_id,
            )
        assert out["budget"]["reconciled_spend_minor"] == 500
        prop = client.post(
            "/v1/proposals",
            headers=auth("operator"),
            json={
                "action_type": "SPEND",
                "subject_type": "campaign",
                "subject_id": "c1",
                "subject_version": 1,
                "payload": {"x": 1},
                "risk": "LOW",
                "monetary_scope_minor": 100,
                "idempotency_key": "es-prop-1-xyz",
            },
        )
        assert prop.json()["status"] == "DENIED" and "emergency stop" in prop.json()["policy_reasons"][0]
        snap = client.get("/v1/agent-presence/snapshot", headers=owner).json()
        assert snap["emergency_stop"] is True
    finally:
        rel = client.post("/v1/emergency-stop/release", json={"reason": "fim do teste"}, headers=owner)
    assert rel.status_code == 200 and rel.json()["active"] is False


def test_only_owner_releases_emergency_stop(client, auth):
    admin, owner = auth("admin"), auth("owner")
    client.post("/v1/emergency-stop", json={"reason": "teste release"}, headers=admin)
    try:
        assert client.post("/v1/emergency-stop/release", json={"reason": "x" * 5}, headers=admin).status_code == 403
    finally:
        assert client.post("/v1/emergency-stop/release", json={"reason": "fim"}, headers=owner).status_code == 200
