"""Perfil Pessoa Física (CPF) — ADR 0006. O perfil não autoriza nada: venda real exige elegibilidade comprovada por
canal (aspecto a aspecto) e gates aprovados PARA o perfil em vigor, com parecer profissional em PF. Inclui tentativas
de contornar os gates (adversarial). Os demais itens da lista de regressão estão em: test_e2e_journey (PF usa o DRY_RUN
de ponta a ponta), test_agents_matrix/test_agent_audit (13 agentes), test_paid_ai_guard (sem IA paga) e
test_gates (ciclo de vida PJ)."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import delete, select, update

from aicommerce.db import session_scope
from aicommerce.governance import seller
from aicommerce.identity.security import issue_access_token
from aicommerce.models import AuditEvent, CertificationGate, ChannelEligibility, GateEvidence, SellerProfile
from aicommerce.providers.registry import resolve
from tests.test_gates import ALL, REQ, approve, evaluate
from tests.test_missions import drain, start

ML_SUPPORTED = {
    "seller_type": "PERSON",
    "aspects": {
        k: {"status": "SUPPORTED", "note": "confirmado na central do vendedor em teste"} for k in seller.ASPECTS
    },
    "sources": [{"url": "https://exemplo.invalid/fonte-oficial-simulada", "kind": "fixture de teste (simulada)"}],
    "scope": "fixture de teste — não é verificação real",
}


@pytest.fixture
def pf_clean():
    """Estado inicial: perfil PF, matriz semeada (nada comprovado), gates não iniciados."""
    yield
    with session_scope() as db:
        db.execute(
            update(CertificationGate).values(
                state="NOT_STARTED", approved_by=None, valid_until=None, evidence_sha256=None, scope={}
            )
        )
        db.execute(delete(ChannelEligibility))
        p = db.get(SellerProfile, 1)
        p.person_type, p.uf, p.fiscal_model, p.restrictions = "PERSON", None, "UNDEFINED", []
        db.flush()
        seller.seed_eligibility(db)


def ml_supported(client, owner):
    r = client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=ML_SUPPORTED)
    assert r.status_code == 200, r.text
    return r.json()


def approve_all(client, op, owner):
    for code in ALL:
        approve(client, op, owner, code)  # evidência do tipo PARECER (exigida em PF)


def test_default_profile_is_person_without_tax_id(client, auth):
    p = client.get("/v1/seller/profile", headers=auth("operator")).json()
    assert p["person_type"] == "PERSON" and p["person_type_label"] == "Pessoa Física (CPF)"
    assert p["tax_id_status"] == "NOT_COLLECTED" and "não é armazenado" in p["tax_id_note"]
    assert "não é autorização" in p["note"]


def test_pf_runs_simulated_mission_without_cnpj(client, auth):
    h = auth("operator")
    mid = start(client, h, "avaliar organizador de gaveta como Pessoa Física", steps=5)
    drain()
    m = client.get(f"/v1/missions/{mid}", headers=h).json()
    assert m["state"] == "COMPLETED" and m["simulated"] is True
    assert {c["agent_id"] for c in m["children"]} == {"luna", "nexus", "finn", "clara", "nova"}


def test_pf_does_not_auto_approve_leg01_and_cpf_is_not_authorization(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    ml_supported(client, owner)  # canal comprovado…
    r = evaluate(client, op)  # …mas nenhum gate: nada liberado
    assert r["decision"] == "DENY" and "LEG-01" in r["unmet"] and r["seller_type"] == "PERSON"
    assert r["summary"] == seller.PF_NOT_HOMOLOGATED
    assert client.get("/v1/gates/LEG-01", headers=op).json()["state"] == "NOT_STARTED"
    assert "LEG-01" in r["required_gates"] and len(r["required_gates"]) >= 6  # PF nunca dispensa gate


def test_full_pf_path_allows_only_with_eligibility_gates_and_opinion(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    approve_all(client, op, owner)
    r = evaluate(client, op)
    assert r["decision"] == "DENY" and set(r["unmet"]) == {"ELIG:MERCADO_LIVRE"}  # gates ok, canal não comprovado
    assert "Pessoa Física ainda não homologada" in r["unmet"]["ELIG:MERCADO_LIVRE"]
    ml_supported(client, owner)  # registrar a elegibilidade derruba o gate do canal (nova revisão)
    assert client.get("/v1/gates/CH-ML", headers=op).json()["state"] == "EXPIRED"
    assert evaluate(client, op)["decision"] == "DENY"
    approve(client, op, owner, "CH-ML")
    r = evaluate(client, op)
    assert r["decision"] == "ALLOW", r
    g = client.get("/v1/gates/LEG-01", headers=op).json()
    assert g["scope"]["seller_types"] == ["PERSON"] and all(x["met"] for x in g["requirements"])
    # 10. aprovar um canal não libera os outros
    for ch in ("SHOPEE", "TIKTOK_SHOP", "WOOCOMMERCE"):
        assert evaluate(client, op, channel=ch)["decision"] == "DENY"


def test_unverified_channel_stays_unverified_and_blocked(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    items = {i["channel"]: i for i in client.get("/v1/seller/eligibility", headers=op).json()["items"]}
    # nada comprovado; TikTok Shop é incompatível com PF pela política oficial publicada (CNPJ obrigatório)
    assert {k: i["status"] for k, i in items.items()} == {
        "WOOCOMMERCE": "UNVERIFIED",
        "MERCADO_LIVRE": "UNVERIFIED",
        "SHOPEE": "UNVERIFIED",
        "TIKTOK_SHOP": "UNSUPPORTED",
    }
    assert not any(i["live_possible"] for i in items.values())
    assert any("OFICIAL" in s["kind"] and "seller-br.tiktok.com" in s["url"] for s in items["TIKTOK_SHOP"]["sources"])
    assert items["TIKTOK_SHOP"]["restrictions"] and items["SHOPEE"]["sources"]
    approve_all(client, op, owner)
    approve(client, op, owner, "CH-SHO")
    r = evaluate(client, op, channel="SHOPEE")
    assert r["decision"] == "DENY" and "ELIG:SHOPEE" in r["unmet"]


def test_unsupported_channel_stays_blocked_even_with_all_gates(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    body = {
        **ML_SUPPORTED,
        "aspects": {**ML_SUPPORTED["aspects"], "registration": {"status": "UNSUPPORTED", "note": "x"}},
    }
    assert client.put("/v1/seller/eligibility/TIKTOK_SHOP", headers=owner, json=body).json()["status"] == "UNSUPPORTED"
    approve_all(client, op, owner)
    approve(client, op, owner, "CH-TTS")
    r = evaluate(client, op, channel="TIKTOK_SHOP")
    assert r["decision"] == "DENY" and "UNSUPPORTED" in r["unmet"]["ELIG:TIKTOK_SHOP"]


def test_without_professional_opinion_fiscal_gate_cannot_be_approved_in_pf(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    with session_scope() as db:  # evidência é só de acréscimo: TAX-02 (importação) não recebe parecer em outro teste
        kinds = set(db.execute(select(GateEvidence.kind).where(GateEvidence.gate_code == "TAX-02")).scalars())
    assert "PARECER" not in kinds
    client.post(
        "/v1/gates/TAX-02/evidence",
        headers=op,
        json={"kind": "DOCUMENTO", "reference": "s3://x/print.png", "notes": "t"},
    )
    client.post("/v1/gates/TAX-02/submit", headers=op)
    r = client.post(
        "/v1/gates/TAX-02/decide",
        headers=owner,
        json={
            "decision": "APPROVED",
            "scope": {},
            "valid_until": "2099-01-01T00:00:00+00:00",
            "note": "sem parecer aqui",
        },
    )
    assert r.status_code == 409 and "parecer" in r.json()["detail"]
    g = client.get("/v1/gates/TAX-02", headers=op).json()
    assert g["state"] == "IN_REVIEW" and any(not x["met"] and "Parecer" in x["label"] for x in g["requirements"])
    assert "Pessoa Física" in g["title"]  # texto do gate adaptado ao perfil (não pede CNPJ)


def test_eligibility_change_invalidates_previous_permission(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    ml_supported(client, owner)
    approve_all(client, op, owner)
    assert evaluate(client, op)["decision"] == "ALLOW"
    worse = {**ML_SUPPORTED, "aspects": {**ML_SUPPORTED["aspects"], "fiscal": {"status": "UNVERIFIED", "note": ""}}}
    r = client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=worse).json()
    assert r["gates_lost"] == ["CH-ML"] and r["status"] == "UNVERIFIED"
    assert evaluate(client, op)["decision"] == "DENY"


def test_profile_change_requires_review_and_pj_remains_compatible(client, auth, pf_clean):
    op, owner = auth("admin"), auth("owner")
    ml_supported(client, owner)
    approve_all(client, op, owner)
    assert evaluate(client, op)["decision"] == "ALLOW"
    r = client.put("/v1/seller/profile", headers=owner, json={"person_type": "COMPANY"}).json()
    assert r["person_type"] == "COMPANY" and set(r["gates_lost"]) == {"LEG-01", "TAX-01", "TAX-03", "CH-ML"}
    assert evaluate(client, op)["decision"] == "DENY"
    # PJ: aprova de novo (aprovação fica vinculada a COMPANY) e a matriz PF não se aplica
    for code in r["gates_lost"]:
        approve(client, op, owner, code)
    assert evaluate(client, op)["decision"] == "ALLOW"
    assert client.get("/v1/gates/LEG-01", headers=op).json()["scope"]["seller_types"] == ["COMPANY"]
    with session_scope() as db:  # aprovação PJ não serve para PF (mesmo sem passar pela API)
        db.get(SellerProfile, 1).person_type = "PERSON"
    r = evaluate(client, op)
    assert r["decision"] == "DENY" and "outro perfil" in r["unmet"]["LEG-01"]


# ─────────────────────────────── tentativas de contornar (adversarial) ───────────────────────────────
def test_bypass_attempts_are_refused(client, auth, make_user, pf_clean):
    op, owner = auth("operator"), auth("owner")
    # operador (e qualquer agente, que age como operador) não registra elegibilidade nem muda o perfil
    assert client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=op, json=ML_SUPPORTED).status_code == 403
    assert client.put("/v1/seller/profile", headers=op, json={"person_type": "COMPANY"}).status_code == 403
    # proprietário sem MFA também não
    u = make_user("owner")
    no_mfa = {"Authorization": f"Bearer {issue_access_token(u['id'], mfa=False)}"}
    r = client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=no_mfa, json=ML_SUPPORTED)
    assert r.status_code in (403, 409)
    # comprovar sem fonte, sem nota, condicional sem restrição, fonte sem https
    assert (
        client.put(
            "/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json={**ML_SUPPORTED, "sources": []}
        ).status_code
        == 409
    )
    no_note = {**ML_SUPPORTED, "aspects": {k: {"status": "SUPPORTED", "note": ""} for k in seller.ASPECTS}}
    assert client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=no_note).status_code == 409
    cond = {
        **ML_SUPPORTED,
        "aspects": {k: {"status": "CONDITIONAL", "note": "condição verificada"} for k in seller.ASPECTS},
    }
    assert client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=cond).status_code == 409
    http = {**ML_SUPPORTED, "sources": [{"url": "http://inseguro.invalid"}]}
    assert client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=http).status_code == 409
    partial = {**ML_SUPPORTED, "aspects": {"registration": {"status": "SUPPORTED", "note": "só o cadastro foi visto"}}}
    assert client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=partial).status_code == 409
    # forjar o perfil na avaliação ou o escopo do gate
    r = client.post("/v1/go-live/evaluate", headers=op, json={**REQ, "seller_type": "COMPANY"}).json()
    assert r["seller_type"] == "PERSON" and r["decision"] == "DENY"
    admin = auth("admin")
    approve(client, admin, owner, "SUP-01")
    client.post(
        "/v1/gates/LEG-01/evidence", headers=admin, json={"kind": "PARECER", "reference": "s3://p.pdf", "notes": "t"}
    )
    client.post("/v1/gates/LEG-01/submit", headers=admin)
    r = client.post(
        "/v1/gates/LEG-01/decide",
        headers=owner,
        json={
            "decision": "APPROVED",
            "scope": {"seller_types": ["PERSON", "COMPANY"]},
            "valid_until": "2099-01-01T00:00:00+00:00",
            "note": "tentando forjar o perfil",
        },
    )
    assert r.status_code == 409 and "escopo" in r.json()["detail"]
    with session_scope() as db:  # nada mudou na matriz
        assert db.get(ChannelEligibility, ("MERCADO_LIVRE", "PERSON")).status == "UNVERIFIED"


def test_tax_id_is_never_stored_logged_or_echoed(client, auth, caplog, pf_clean):
    fake = "000.000.000-00"  # CPF claramente fictício (fixture), nunca identidade real
    caplog.set_level(logging.DEBUG)
    r = client.put("/v1/seller/profile", headers=auth("owner"), json={"uf": "sp", "cpf": fake, "tax_id": fake})
    assert r.status_code == 200 and fake not in r.text and r.json()["uf"] == "SP"
    assert fake not in caplog.text
    with session_scope() as db:
        rows = db.execute(select(AuditEvent).where(AuditEvent.action == "seller.profile.updated")).scalars().all()
        assert rows and all(fake not in str(e.payload) for e in rows)
        assert not hasattr(db.get(SellerProfile, 1), "cpf")


def test_simulation_uses_mock_providers_for_every_channel():
    for ch in ("WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"):
        r = resolve("PUBLISH_LISTING", "DRY_RUN", ch)
        assert r.provider is not None and type(r.provider).__name__.startswith("Mock"), ch  # nada real em DRY_RUN


def test_seed_upgrades_only_its_own_rows_and_bumps_version(pf_clean):
    with session_scope() as db:
        tt = db.get(ChannelEligibility, ("TIKTOK_SHOP", "PERSON"))
        tt.policy_version, tt.status, tt.version = "2026.10.pf1", "UNVERIFIED", 3  # linha antiga do seed
        ml = db.get(ChannelEligibility, ("MERCADO_LIVRE", "PERSON"))
        ml.policy_version, ml.updated_by, ml.status = "2026.10.pf1", "owner-uuid", "SUPPORTED"  # registrada com MFA
    with session_scope() as db:
        seller.seed_eligibility(db)
    with session_scope() as db:
        tt = db.get(ChannelEligibility, ("TIKTOK_SHOP", "PERSON"))
        ml = db.get(ChannelEligibility, ("MERCADO_LIVRE", "PERSON"))
        assert tt.status == "UNSUPPORTED" and tt.version == 4 and tt.policy_version == seller.ELIGIBILITY_POLICY_VERSION
        assert ml.status == "SUPPORTED" and ml.updated_by == "owner-uuid"  # o que o proprietário registrou não muda


def test_person_opinion_gate_accepts_owner_risk_acceptance_only_with_restrictions(client, auth, pf_clean):
    owner, admin = auth("owner"), auth("admin")
    with session_scope() as db:
        db.get(SellerProfile, 1).person_type = "PERSON"
    client.post(
        "/v1/gates/TAX-02/evidence",
        headers=admin,
        json={"kind": "ACEITE_RISCO", "reference": "docs/EVIDENCIA_TAX-02.md", "notes": "proprietário assume o risco"},
    )
    assert client.post("/v1/gates/TAX-02/submit", headers=admin).status_code == 200
    body = {"scope": {}, "valid_until": "2099-01-01T00:00:00+00:00", "note": "piloto restrito, risco assumido"}
    r = client.post("/v1/gates/TAX-02/decide", headers=owner, json={**body, "decision": "APPROVED"})
    assert r.status_code == 409 and "ACEITE_RISCO" in r.json()["detail"]
    r = client.post(
        "/v1/gates/TAX-02/decide",
        headers=owner,
        json={**body, "decision": "APPROVED_WITH_RESTRICTIONS", "scope": {"restrictions": ["só piloto"]}},
    )
    assert r.status_code == 200, r.text
    with session_scope() as db:
        g = db.get(CertificationGate, "TAX-02")
        assert g.state == "APPROVED_WITH_RESTRICTIONS"
        assert g.scope["restrictions"] == ["só piloto", "aceite de risco do proprietário, sem parecer profissional"]
