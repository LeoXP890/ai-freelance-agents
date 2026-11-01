"""Phase 2 supplier lead screening is evidence-based and never vendor approval."""

from aicommerce.ai.supplier_prospects import LEADS, public_leads


def test_pf_screening_excludes_officially_company_only_supplier():
    leads = public_leads(person_only=True)
    assert any(lead["name"] == "DropSystem" for lead in leads)
    assert all(lead["name"] != "Dropify" for lead in leads)


def test_conflicting_pf_requirements_not_marked_as_eligible():
    dslite = next(lead for lead in LEADS if lead["name"] == "DSlite")
    assert dslite["cpf_public_claim"] == "CONFLICTING"
    assert any("CPF" in reason for reason in dslite["critical_unknowns"])


def test_no_lead_claims_final_channel_approval_or_real_prices():
    for lead in LEADS:
        assert lead["cpf_public_claim"] in {
            "PUBLIC_CLAIM_YES",
            "CONFLICTING",
            "UNVERIFIED",
            "COMPANY_ONLY",
        }
        assert lead["source_url"].startswith("https://")
        assert lead["critical_unknowns"]
        assert "cost_minor" not in lead
        assert "marketplace_gate_approved" not in lead
        assert "stock_qty" not in lead


def test_category_screen_excludes_unrelated_specialist_without_claiming_generic_catalog():
    leads = public_leads(category="organização de cabos", person_only=True)
    assert "Pechincha Livre" not in {lead["name"] for lead in leads}
    assert leads
    assert all(lead["category_fit"] == "CATALOG_UNVERIFIED" for lead in leads)
    assert all("cost_minor" not in lead and "stock_qty" not in lead for lead in leads)
    footwear = public_leads(category="tênis e calçados", person_only=True)
    assert next(lead for lead in footwear if lead["name"] == "Pechincha Livre")["category_fit"] == (
        "VENDOR_CATEGORY_CLAIM"
    )


def test_switching_to_company_profile_retains_company_only_as_research_not_approval():
    pf = public_leads(person_only=True, category="acessórios de escritório")
    pj = public_leads(person_only=False, category="acessórios de escritório")
    assert "Dropify" not in {lead["name"] for lead in pf}
    company = next(lead for lead in pj if lead["name"] == "Dropify")
    assert company["cpf_public_claim"] == "COMPANY_ONLY"
    assert company["category_fit"] == "CATALOG_UNVERIFIED"
    assert "approved" not in company


def test_lead_evidence_is_independent_between_runs():
    a, b = public_leads(), public_leads()
    a[0]["critical_unknowns"].clear()
    assert b[0]["critical_unknowns"] and LEADS[0]["critical_unknowns"]


def test_nexus_receives_category_screen_and_unverified_evidence(db):
    from aicommerce.ai.specs import SPECS

    ctx = SPECS["supplier.discover"].build(db, {"category": "organização de cabos"})
    assert ctx.facts["tipo_vendedor"] == "PESSOA_FISICA"
    assert ctx.facts["aderencia_categoria_confirmada"] is False
    assert all(s["title"] != "Pechincha Livre" for s in ctx.sources)
    assert all(s["category_fit"] == "CATALOG_UNVERIFIED" for s in ctx.sources if s["id"].startswith("P"))
    assert "precos_confirmados" in ctx.facts and not ctx.facts["precos_confirmados"]


def test_supplier_name_matches_title_or_domain_but_not_unrelated_names():
    from aicommerce.ai.specs import _same_supplier

    src = {"title": "bellajanelaatacado.com.br", "url": "https://bellajanelaatacado.com.br/cortinas"}
    assert _same_supplier("Bella Janela", src)
    assert _same_supplier("Bella Janela Atacado", src)
    assert _same_supplier("Universo Drop", {"title": "Universo Drop — Dropshipping Nacional", "url": "https://x.com"})
    assert not _same_supplier("Fornecedor Inventado", src)
    assert not _same_supplier("AB", {"title": "AB", "url": ""})
