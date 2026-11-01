"""Publicly documented supplier leads for the Phase 2 research shortlist.

Official vendor claims ARE NOT verified account eligibility, supplier contracts,
quotes, tax approval, marketplace shipping permission, or current inventory.
Review dates must be renewed before recommendations become operational.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NotRequired, TypedDict


class SupplierLead(TypedDict):
    name: str
    url: str
    category: str
    cpf_public_claim: str
    direct_dispatch_claim: bool
    documented_api: str
    source_url: str
    checked_on: str
    critical_unknowns: list[str]
    category_fit: NotRequired[str]  # orientação de pesquisa, nunca prova de SKUs disponíveis


LEADS: tuple[SupplierLead, ...] = (
    {
        "name": "Dropi / Dropnacional",
        "url": "https://www.dropi.com.br/fornecedores/",
        "category": "catalogo geral",
        "cpf_public_claim": "UNVERIFIED",
        "direct_dispatch_claim": True,
        "documented_api": "VENDOR_CLAIM_YES",
        "source_url": "https://dropi.sak.com.br/guia-para-fornecedores-dropnacional",
        "checked_on": "2026-10-26",
        "critical_unknowns": [
            "CPF acceptance must be confirmed by each supplier; platform capability is not a contract",
            "formal packing, marketplace label, direct dispatch and return responsibilities",
            "Bling/Tiny tracking integration access, real inventory, destination-specific freight and SLA",
            "supplier identity, category/SKU eligibility and fiscal model for the owner's profile",
        ],
    },
    {
        "name": "DropSystem",
        "url": "https://dropsystem.com.br/",
        "category": "catalogo geral e personalizaveis",
        "cpf_public_claim": "PUBLIC_CLAIM_YES",
        "direct_dispatch_claim": True,
        "documented_api": "UNVERIFIED",
        "source_url": "https://dropsystem.com.br/",
        "checked_on": "2026-10-25",
        "critical_unknowns": [
            "CPF eligible for intended habitual resale and channel",
            "product categories and purchase prices",
            "carrier label support, dispatch SLA, live stock",
            "supplier fiscal documents, return flows, formal agreement",
        ],
    },
    {
        "name": "DSlite",
        "url": "https://www.dslite.com.br/",
        "category": "catalogo geral",
        "cpf_public_claim": "CONFLICTING",
        "direct_dispatch_claim": True,
        "documented_api": "VENDOR_CLAIM_YES",
        "source_url": "https://app.dslite.com.br/faq/2",
        "checked_on": "2026-10-25",
        "critical_unknowns": [
            "special CPF plan vs FAQ requiring CNPJ and XML invoice",
            "which suppliers permit CPF and exact operations",
            "SKU access, prices, shipping labels, catalog API entitlements",
        ],
    },
    {
        "name": "Pechincha Livre",
        "url": "https://pechinchalivre.com.br/",
        "category": "calcados",
        "cpf_public_claim": "UNVERIFIED",
        "direct_dispatch_claim": True,
        "documented_api": "UNVERIFIED",
        "source_url": "https://pechinchalivre.com.br/",
        "checked_on": "2026-10-25",
        "critical_unknowns": [
            "CPF registration and professional resale eligibility",
            "integration contracts and allowed channels",
            "actual SKU prices, dispatch SLA, return and invoice process",
        ],
    },
    {
        "name": "Dropify",
        "url": "https://dropify.com.br/",
        "category": "catalogo geral",
        "cpf_public_claim": "COMPANY_ONLY",
        "direct_dispatch_claim": True,
        "documented_api": "VENDOR_CLAIM_YES",
        "source_url": "https://dropify.com.br/perguntas-respostas",
        "checked_on": "2026-10-25",
        "critical_unknowns": ["requires CNPJ and state registration according to provider FAQ"],
    },
)


def _terms(value: str) -> set[str]:
    """Small, conservative category screen: non-matches are omitted, never inferred."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(ch))
    return set(re.findall(r"[a-z0-9]{4,}", plain))


def _category_fit(lead: SupplierLead, requested: str) -> str:
    """Distinguish vendor-described specialty from generic unverified catalog claims."""
    scope = _terms(lead["category"])
    if "catalogo" in scope and "geral" in scope:
        return "CATALOG_UNVERIFIED"
    # These are aliases for *category search*, not verification of products or stock.
    if "calcados" in scope:
        scope.update({"calcado", "sapato", "sapatos", "tenis", "sandalia", "sandalias", "botas"})
    return "VENDOR_CATEGORY_CLAIM" if scope & _terms(requested) else "OUT_OF_SCOPE"


def public_leads(
    *, person_only: bool = True, include_ineligible: bool = False, category: str | None = None
) -> list[SupplierLead]:
    """Only research leads; PF constraints and category claims are NOT commercial approval.

    Category-specific leads with no topical overlap are excluded; generic platforms
    stay visible as CATALOG_UNVERIFIED, without pretending their SKUs were checked.
    """
    results: list[SupplierLead] = []
    for source in LEADS:
        if person_only and not include_ineligible and source["cpf_public_claim"] == "COMPANY_ONLY":
            continue
        fit = _category_fit(source, category) if category else "NOT_REQUESTED"
        if fit == "OUT_OF_SCOPE":
            continue
        lead = source.copy()
        lead["critical_unknowns"] = list(source["critical_unknowns"])
        lead["category_fit"] = fit
        results.append(lead)
    return results
