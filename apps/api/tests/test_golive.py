import datetime as dt

import pytest

from aicommerce.governance.golive import (
    Channel,
    FulfillmentMode,
    GateSnapshot,
    GoLiveRequest,
    SupplierOrigin,
    evaluate,
    required_gates,
)
from aicommerce.governance.seller import SellerContext

NOW = dt.datetime(2026, 10, 23, tzinfo=dt.UTC)
REQ = GoLiveRequest(Channel.MERCADO_LIVRE, SupplierOrigin.NATIONAL, FulfillmentMode.DROPSHIP_SUPPLIER, "SP")
# Testes do desenho original (PJ, sem matriz cadastrada: conta apta comprovada pelo gate do canal) — compatibilidade.
PJ = SellerContext("COMPANY", {})


def approved(code, **kw):
    base = dict(
        state="APPROVED",
        approved_by="contador@x",
        evidence_sha256="a" * 64,
        policy_version="2026.10.1",
        valid_until=NOW + dt.timedelta(days=30),
        scope={},
    )
    base.update(kw)
    return GateSnapshot(code, **base)


def all_approved(req, **kw):
    return {c: approved(c, **kw) for c in required_gates(req)}


def test_required_gates_matrix():
    assert required_gates(REQ) == ["CH-ML", "LEG-01", "OPS-01", "SUP-01", "TAX-01", "TAX-03"]
    xb = GoLiveRequest(Channel.TIKTOK_SHOP, SupplierOrigin.INTERNATIONAL, FulfillmentMode.CROSS_BORDER, "SP")
    assert "TAX-02" in required_gates(xb) and "CH-TTS" in required_gates(xb) and "TAX-01" not in required_gates(xb)


def test_no_gates_means_deny():
    r = evaluate(REQ, {}, PJ, NOW)
    assert r.decision == "DENY" and set(r.unmet) == set(r.required_gates)


def test_all_valid_gates_allow():
    assert evaluate(REQ, all_approved(REQ), PJ, NOW).decision == "ALLOW"


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"state": "IN_REVIEW"}, "estado"),
        ({"state": "EXPIRED"}, "estado"),
        ({"approved_by": None}, "approved_by"),
        ({"evidence_sha256": None}, "hash"),
        ({"valid_until": NOW - dt.timedelta(seconds=1)}, "expirada"),
        ({"valid_until": None}, "validade"),
        ({"scope": {"ufs": ["RJ"]}}, "escopo"),
        ({"scope": {"channels": ["SHOPEE"]}}, "escopo"),
    ],
)
def test_any_invalid_gate_denies(override, reason):
    gates = all_approved(REQ)
    gates["TAX-01"] = approved("TAX-01", **override)
    r = evaluate(REQ, gates, PJ, NOW)
    assert r.decision == "DENY" and reason in r.unmet["TAX-01"]


def test_approved_with_restrictions_reports_restrictions():
    gates = all_approved(REQ)
    gates["CH-ML"] = approved("CH-ML", state="APPROVED_WITH_RESTRICTIONS", scope={"restrictions": "só SP"})
    r = evaluate(REQ, gates, PJ, NOW)
    assert r.decision == "ALLOW" and r.restrictions == {"CH-ML": "só SP"}


@pytest.mark.parametrize("channel", ["WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"])
def test_api_denies_every_channel_before_homologation(client, auth, channel):
    r = client.post(
        "/v1/go-live/evaluate",
        headers=auth("operator"),
        json={"channel": channel, "supplier_origin": "NATIONAL", "fulfillment_mode": "DROPSHIP_SUPPLIER", "uf": "sp"},
    )
    assert r.status_code == 200
    body = r.json()
    gates_unmet = {k: v for k, v in body["unmet"].items() if not k.startswith("ELIG:")}
    assert body["decision"] == "DENY" and all("NOT_STARTED" in v for v in gates_unmet.values())
    assert body["seller_type"] == "PERSON" and f"ELIG:{channel}" in body["unmet"]  # perfil PF semeado, nada comprovado


def test_gate_catalog_seeded(client, auth):
    items = client.get("/v1/gates", headers=auth("operator")).json()["items"]
    assert {g["code"] for g in items} >= {
        "LEG-01",
        "TAX-01",
        "TAX-02",
        "TAX-03",
        "SUP-01",
        "CH-WOO",
        "CH-ML",
        "CH-SHO",
        "CH-TTS",
        "OPS-01",
    }
    assert all(g["state"] == "NOT_STARTED" for g in items)
