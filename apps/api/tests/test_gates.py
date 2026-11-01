import datetime as dt

import pytest
from sqlalchemy import update

from aicommerce.db import session_scope
from aicommerce.governance import gates
from aicommerce.identity.security import issue_access_token
from aicommerce.models import CertificationGate, SellerProfile

ALL = ["LEG-01", "TAX-01", "TAX-03", "SUP-01", "CH-ML", "OPS-01"]  # gates do ML + nacional + dropship
REQ = {"channel": "MERCADO_LIVRE", "supplier_origin": "NATIONAL", "fulfillment_mode": "DROPSHIP_SUPPLIER", "uf": "SP"}


def future(days=30):
    return (dt.datetime.now(dt.UTC) + dt.timedelta(days=days)).isoformat()


def approve(client, op, owner, code, **scope):
    assert (
        client.post(
            f"/v1/gates/{code}/evidence",
            headers=op,
            json={"kind": "PARECER", "reference": f"s3://evidencias/{code}.pdf", "notes": "teste"},
        ).status_code
        == 201
    )
    assert client.post(f"/v1/gates/{code}/submit", headers=op).json()["state"] == "IN_REVIEW"
    r = client.post(
        f"/v1/gates/{code}/decide",
        headers=owner,
        json={"decision": "APPROVED", "scope": scope, "valid_until": future(), "note": "conferido com o contador"},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def reset_gates():
    yield
    with session_scope() as db:  # testes compartilham o banco: devolve os gates ao estado inicial
        db.execute(
            update(CertificationGate).values(
                state="NOT_STARTED", approved_by=None, valid_until=None, evidence_sha256=None, scope={}
            )
        )


@pytest.fixture
def pj_profile():
    """Desenho original (Pessoa Jurídica): o ciclo de vida dos gates continua igual — compatibilidade PJ."""
    with session_scope() as db:
        db.get(SellerProfile, 1).person_type = "COMPANY"
    yield
    with session_scope() as db:
        db.get(SellerProfile, 1).person_type = "PERSON"


def evaluate(client, h, **over):
    return client.post("/v1/go-live/evaluate", headers=h, json={**REQ, **over}).json()


def test_full_lifecycle_unlocks_and_revocation_relocks(client, auth, reset_gates, pj_profile):
    op, owner = auth("admin"), auth("owner")
    assert evaluate(client, op)["decision"] == "DENY"
    for code in ALL:
        g = approve(client, op, owner, code)
        assert g["state"] == "APPROVED" and g["evidence_sha256"] == g["current_evidence_sha256"]
        assert g["policy_version"] and g["approved_by"]
    assert evaluate(client, op)["decision"] == "ALLOW"
    cursor = client.get("/v1/agent-presence/snapshot", headers=op).json()["as_of_seq"]
    r = client.post("/v1/gates/CH-ML/revoke", headers=owner, json={"reason": "conta suspensa pelo canal"})
    assert r.json()["state"] == "REJECTED"
    assert evaluate(client, op)["decision"] == "DENY"
    evs = client.get(f"/v1/office/events?after_seq={cursor}", headers=op).json()["items"]
    assert any(e["type"] == "go_live.revoked" and e["payload"]["gate"] == "CH-ML" for e in evs)


def test_scope_restricts_and_expiry_relocks(client, auth, reset_gates, pj_profile):
    op, owner = auth("admin"), auth("owner")
    for code in ALL:
        approve(client, op, owner, code, **({"ufs": ["SP"]} if code == "OPS-01" else {}))
    assert evaluate(client, op)["decision"] == "ALLOW"
    assert evaluate(client, op, uf="RJ")["decision"] == "DENY"  # fora do escopo aprovado
    with session_scope() as db:
        db.get(CertificationGate, "TAX-01").valid_until = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    with session_scope() as db:
        assert gates.expire_due(db) == 1
    assert client.get("/v1/gates/TAX-01", headers=op).json()["state"] == "EXPIRED"
    assert evaluate(client, op)["decision"] == "DENY"


def test_decision_rules(client, auth, make_user, reset_gates):
    op, owner = auth("admin"), auth("owner")
    assert client.post("/v1/gates/LEG-01/submit", headers=op).status_code == 409  # sem evidência
    client.post("/v1/gates/LEG-01/evidence", headers=op, json={"kind": "DOCUMENTO", "reference": "s3://cnpj.pdf"})
    client.post("/v1/gates/LEG-01/submit", headers=op)
    body = {"decision": "APPROVED", "scope": {}, "valid_until": future(), "note": "ok ok ok ok ok"}
    assert client.post("/v1/gates/LEG-01/decide", headers=op, json=body).status_code == 403  # admin não aprova
    no_mfa = {"Authorization": f"Bearer {issue_access_token(make_user('owner')['id'], mfa=False)}"}
    assert client.post("/v1/gates/LEG-01/decide", headers=no_mfa, json=body).status_code == 403
    past = {**body, "valid_until": (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()}
    assert "validade futura" in client.post("/v1/gates/LEG-01/decide", headers=owner, json=past).json()["detail"]
    restr = {**body, "decision": "APPROVED_WITH_RESTRICTIONS"}
    assert "restrições" in client.post("/v1/gates/LEG-01/decide", headers=owner, json=restr).json()["detail"]
    bad_scope = {**body, "scope": {"qualquer": 1}}
    assert client.post("/v1/gates/LEG-01/decide", headers=owner, json=bad_scope).status_code == 409
    assert client.post("/v1/gates/LEG-01/decide", headers=owner, json=body).json()["state"] == "APPROVED"
    r = client.post("/v1/gates/LEG-01/evidence", headers=op, json={"kind": "DOCUMENTO", "reference": "s3://x"})
    assert r.status_code == 409  # aprovado: base de evidência congelada
    assert client.post("/v1/gates/NAO-EXISTE/submit", headers=op).status_code == 404


def test_evidence_is_append_only(db):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM gate_evidence"))


def test_sales_dashboard(client, auth):
    from tests.test_orders import order  # noqa: F401

    r = client.get("/v1/dashboard/sales?days=30", headers=auth("viewer"))
    assert r.status_code == 200
    body = r.json()
    assert (
        len(body["daily"]) == 30
        and "contribution_minor" in body["totals"]
        and "fictícia" in body["note"]
        and "pagos" in body["note"]
    )
    for c in body["channels"]:
        assert c["net_revenue_minor"] == c["revenue_minor"] - c["refund_minor"] - c["fees_minor"]
        assert c["contribution_minor"] == c["net_revenue_minor"] - c["cost_minor"]
    assert client.get("/v1/dashboard/sales", headers={"Authorization": "Bearer x"}).status_code == 401
