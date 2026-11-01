"""Encerramento do desenvolvimento — dois ajustes de governança (ADR 0006, adendo):

1. Elegibilidade CONDITIONAL não é autorização irrestrita: condições estruturadas (UF, modalidade, origem, produto,
   fornecedor) são conferidas contra a operação concreta; violada ou não comprovável → DENY; CONDITIONAL sem
   condição → DENY; a mesma checagem roda na revalidação imediatamente antes da execução (executor → reevaluate).
2. Mudança material no perfil (UF, modelo fiscal, restrições, tipo) derruba só as homologações que dependem dela,
   de forma auditada; e a aprovação guarda a impressão do perfil, então permissão obsoleta nunca é reutilizada —
   mesmo que alguém altere o perfil por fora da API.

Nenhuma operação real é executada: a revalidação usa uma proposta LIVE em memória (não persistida)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aicommerce.db import session_scope
from aicommerce.governance import approvals
from aicommerce.governance.policy import Decision
from aicommerce.models import ApprovalProposal, AuditEvent, ChannelEligibility, SellerProfile
from tests.test_gates import REQ, evaluate
from tests.test_seller_profile import ML_SUPPORTED, approve_all, pf_clean  # noqa: F401

COND = {**ML_SUPPORTED, "restrictions": ["só SP, envio pelo fornecedor nacional"]}


def ml(client, owner, body):
    r = client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def conditional(**conds):
    aspects = {**ML_SUPPORTED["aspects"], "habitual_sales": {"status": "CONDITIONAL", "note": "condição verificada"}}
    return {**COND, "aspects": aspects, "conditions": [{"field": k, "allowed": v} for k, v in conds.items()]}


def all_live(client, op, owner, body):
    ml(client, owner, body)
    approve_all(client, op, owner)  # LEG-01, TAX-01, TAX-03, SUP-01, CH-ML, OPS-01 aprovados para PF


def live_proposal(**over):
    """Proposta LIVE só em memória: serve para exercitar a revalidação do executor sem executar nada."""
    ctx = {**REQ, **over}
    return ApprovalProposal(
        action_type="PUBLISH_LISTING",
        risk_score=50,
        evidence_refs=["listing_version:x"],
        monetary_scope_minor=0,
        currency="BRL",
        mode="LIVE",
        go_live_context=ctx,
    )


# ───────────────────────────── 1. CONDITIONAL com condições verificáveis ─────────────────────────────
def test_conditional_without_verifiable_conditions_is_refused(client, auth, pf_clean):  # noqa: F811
    owner = auth("owner")
    body = conditional()
    body["conditions"] = []
    r = client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=body)
    assert r.status_code == 409 and "condições verificáveis" in r.json()["detail"]
    bad = {**conditional(), "conditions": [{"field": "faturamento_maximo", "allowed": ["10"]}]}
    assert client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=bad).status_code == 422


def test_conditions_are_enforced_against_the_concrete_operation(client, auth, pf_clean):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    all_live(client, op, owner, conditional(ufs=["SP"], fulfillment_modes=["DROPSHIP_SUPPLIER"]))
    assert evaluate(client, op)["decision"] == "ALLOW"  # SP + venda à ordem: dentro das condições
    r = evaluate(client, op, uf="RJ")
    assert r["decision"] == "DENY" and "condição ufs violada" in r["unmet"]["ELIG:MERCADO_LIVRE"]
    r = evaluate(client, op, fulfillment_mode="OWN_STOCK")
    assert r["decision"] == "DENY" and "fulfillment_modes" in r["unmet"]["ELIG:MERCADO_LIVRE"]


def test_condition_that_cannot_be_proven_denies(client, auth, pf_clean):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    all_live(client, op, owner, conditional(product_ids=["produto-autorizado"]))
    r = evaluate(client, op)  # a operação não informa o produto: não dá para comprovar a condição
    assert r["decision"] == "DENY" and "não pode ser comprovada" in r["unmet"]["ELIG:MERCADO_LIVRE"]
    assert evaluate(client, op, product_id="outro")["decision"] == "DENY"
    assert evaluate(client, op, product_id="produto-autorizado")["decision"] == "ALLOW"


def test_bypass_conditional_row_written_outside_the_api_still_denies(client, auth, pf_clean):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    all_live(client, op, owner, conditional(ufs=["SP"]))
    assert evaluate(client, op)["decision"] == "ALLOW"
    for conds in ([], [{"field": "renda", "allowed": ["x"]}], [{"field": "ufs", "allowed": []}]):
        with session_scope() as db:  # alguém grava direto no banco, sem a validação da API
            db.get(ChannelEligibility, ("MERCADO_LIVRE", "PERSON")).conditions = conds
        assert evaluate(client, op)["decision"] == "DENY", conds


def test_revalidation_before_execution_applies_conditions_and_audits_reasons(client, auth, pf_clean):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    all_live(client, op, owner, conditional(ufs=["SP"]))
    with session_scope() as db:
        assert approvals.reevaluate(db, live_proposal()).decision != Decision.DENY  # tudo válido: não nega
        res = approvals.reevaluate(db, live_proposal(uf="RJ"))  # o que o executor roda antes de agir
        assert res.decision == Decision.DENY
        assert any("condição ufs violada" in r for r in res.reasons)  # motivo detalhado vai para a auditoria
    with session_scope() as db:  # condição muda depois da aprovação → a mesma proposta é negada na execução
        db.get(ChannelEligibility, ("MERCADO_LIVRE", "PERSON")).conditions = [{"field": "ufs", "allowed": ["MG"]}]
    with session_scope() as db:
        res = approvals.reevaluate(db, live_proposal())
        assert res.decision == Decision.DENY and any("ELIG:MERCADO_LIVRE" in r for r in res.reasons)


# ───────────────────────────── 2. mudança material invalida o que depende dela ─────────────────────────────
@pytest.mark.parametrize(
    "change,lost",
    [
        ({"uf": "SP"}, {"LEG-01", "TAX-01", "TAX-03"}),
        ({"fiscal_model": "PERSON_PENDING_OPINION"}, {"LEG-01", "TAX-01", "TAX-03"}),
        ({"restrictions": ["sem eletrônicos"]}, {"LEG-01"}),
    ],
)
def test_material_profile_change_invalidates_only_dependent_gates(client, auth, pf_clean, change, lost):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    all_live(client, op, owner, ML_SUPPORTED)
    assert evaluate(client, op)["decision"] == "ALLOW"
    r = client.put("/v1/seller/profile", headers=owner, json=change).json()
    assert set(r["gates_lost"]) == lost
    assert client.get("/v1/gates/CH-ML", headers=op).json()["state"] == "APPROVED"  # canal não depende disso
    e = evaluate(client, op)
    assert e["decision"] == "DENY" and lost <= set(e["unmet"])
    with session_scope() as db:
        ev = db.execute(
            select(AuditEvent)
            .where(AuditEvent.action == "seller.profile.updated")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        ).scalar_one()
        assert set(ev.payload["gates_lost"]) == lost and ev.payload["changed"]
        revoked = (
            db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "gate.revoked")
                .order_by(AuditEvent.id.desc())
                .limit(len(lost))
            )
            .scalars()
            .all()
        )
        assert {x.subject_id for x in revoked} == lost and all("depende" in x.payload["reason"] for x in revoked)


def test_irrelevant_or_identical_change_keeps_approvals(client, auth, pf_clean):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    with session_scope() as db:
        db.get(SellerProfile, 1).uf = "SP"
    all_live(client, op, owner, ML_SUPPORTED)
    r = client.put("/v1/seller/profile", headers=owner, json={"uf": "sp", "fiscal_model": "UNDEFINED"}).json()
    assert r["gates_lost"] == [] and evaluate(client, op)["decision"] == "ALLOW"


def test_obsolete_permission_is_not_reused_even_if_profile_changes_outside_the_api(client, auth, pf_clean):  # noqa: F811
    op, owner = auth("admin"), auth("owner")
    all_live(client, op, owner, ML_SUPPORTED)
    assert evaluate(client, op)["decision"] == "ALLOW"
    with session_scope() as db:  # sem passar pela API: nenhuma invalidação foi disparada…
        db.get(SellerProfile, 1).fiscal_model = "PERSON_PENDING_OPINION"
    r = evaluate(client, op)  # …mas a impressão do perfil não bate mais: a aprovação antiga não vale
    assert r["decision"] == "DENY" and "mudou desde a aprovação" in r["unmet"]["LEG-01"]
    with session_scope() as db:
        res = approvals.reevaluate(db, live_proposal())
        assert res.decision == Decision.DENY  # e o executor também nega antes de agir
    with session_scope() as db:
        db.get(SellerProfile, 1).fiscal_model = "UNDEFINED"
    assert evaluate(client, op)["decision"] == "ALLOW"  # volta ao estado aprovado: a impressão bate de novo
