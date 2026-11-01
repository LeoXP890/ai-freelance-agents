"""Publicação pela plataforma parceira (Dropi): aprovação humana, sem job, resultado registrado pelo agente."""

import uuid

import pytest
from sqlalchemy import select

from aicommerce.ai import ml_market_research as mr
from aicommerce.commerce import platform_publish
from aicommerce.db import session_scope
from aicommerce.models import ApprovalProposal, ExecutionJob, Opportunity, SellerProfile
from tests.test_approvals import decide

MARKET = {
    "offers": [
        {
            "title": "Mercado Livre - Kit 3 Formas",
            "url": "https://www.mercadolivre.com.br/a",
            "text": "R$ 99,90 com frete grátis",
        },
        {"title": "Shopee - Kit 3 Formas", "url": "https://shopee.com.br/b", "text": "R$ 109,90"},
        {"title": "Magalu - Kit 3 Formas", "url": "https://www.magazineluiza.com.br/c", "text": "R$ 119,90"},
    ]
}


@pytest.fixture(autouse=True)
def clear_tool_cache():
    from sqlalchemy import delete

    from aicommerce.models import ToolCache

    with session_scope() as db:
        db.execute(delete(ToolCache).where(ToolCache.tool == "market.price_check"))


@pytest.fixture(autouse=True)
def fake_ml(monkeypatch, clear_tool_cache):
    """Tarifa como a API do Mercado Livre: 11,5% e, abaixo de R$ 79, mais R$ 6,25 fixos (já somados em sale_fee)."""
    from aicommerce.ai.tools import ml

    def get(path):
        assert path.startswith("/sites/MLB/listing_prices"), path
        price = float(path.split("price=")[1].split("&")[0])
        fixed = 6.25 if price < 79 else 0.0
        return [
            {
                "listing_type_id": "gold_special",
                "sale_fee_amount": round(price * 0.115 + fixed, 2),
                "sale_fee_details": {"percentage_fee": 11.5, "fixed_fee": fixed},
            }
        ]

    monkeypatch.setattr(ml, "_get", get)
    from aicommerce.ai import specs

    monkeypatch.setattr(
        specs, "_web_sources", lambda q, limit=3, agent_id="luna", prompt=None: (MARKET["offers"], None)
    )


def opportunity():
    item = {
        "title": "Kit 3 Formas de Bolo",
        "category": "Piloto Dropi",
        "rationale": "teste",
        "contraindications": [],
        "evidence_quality": "MEDIUM",
        "score": 80,
        "sources": [
            {
                "id": f"dropi:{uuid.uuid4().hex[:10]}",
                "title": "Dropi",
                "url": "https://app.dropi.com.br/x",
                "date": None,
                "simulated": False,
                "market": {
                    "supplier": "Gringa",
                    "supplier_cost": 26.45,
                    "gtin": "6925073145868",
                    "supplier_freight": 10.0,
                    "category_id": "MLB193527",
                    "fee": {"percentage_fee": 12.5},
                    "shipping": {"seller_cost": 24.35, "box": {"cm": [], "grams": 0}},  # sem medidas: usa a cotação
                    "pictures": [],
                },
            }
        ],
    }
    with session_scope() as db:
        db.get(SellerProfile, 1).uf = "PR"
        return mr.save(db, [item])["opportunity_ids"][0]


def test_platform_publication_needs_human_approval_and_no_executor_job(client, auth):
    op, owner = auth("operator"), auth("owner")
    oid = opportunity()
    r = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=op)
    assert r.status_code == 201, r.text
    prop = r.json()
    assert prop["action_type"] == "PUBLISH_VIA_PLATFORM" and prop["status"] == "PENDING_APPROVAL"
    with session_scope() as db:
        payload = db.get(ApprovalProposal, uuid.UUID(prop["id"])).payload
    assert payload["estimated_profit_minor"] == 10990 - 1264 - 2435 - 1000 - 2645  # tarifa, frete ML, fornecedor, custo
    labels = [line["key"] for line in payload["economics"]["lines"]]
    assert labels == ["price", "fee_pct", "seller_shipping", "supplier_freight", "cost"]
    assert decide(client, owner, prop).status_code == 200
    with session_scope() as db:
        p = db.get(ApprovalProposal, uuid.UUID(prop["id"]))
        assert p.status == "AUTHORIZED"
        assert db.execute(select(ExecutionJob).where(ExecutionJob.proposal_id == p.id)).first() is None
        platform_publish.record_published(
            db, p.id, actor="luna", listing_url="https://produto.mercadolivre.com.br/MLB-1", listing_id="MLB1"
        )
    with session_scope() as db:
        assert db.get(ApprovalProposal, uuid.UUID(prop["id"])).status == "SUCCEEDED"
        assert db.get(Opportunity, uuid.UUID(oid)).status == "PUBLISHED"


def test_cannot_record_publication_without_approval(client, auth):
    op = auth("operator")
    oid = opportunity()
    prop = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=op).json()
    import pytest

    with session_scope() as db, pytest.raises(platform_publish.PlatformPublishError, match="PENDING_APPROVAL"):
        platform_publish.record_published(
            db, uuid.UUID(prop["id"]), actor="luna", listing_url="https://x.com/1", listing_id="1"
        )


def test_live_without_go_live_gates_is_denied(client, auth, monkeypatch):
    from aicommerce.config import RuntimeMode, get_settings

    op = auth("operator")
    oid = opportunity()
    monkeypatch.setattr(get_settings(), "mode", RuntimeMode.LIVE)
    r = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=op)
    assert r.status_code == 201 and r.json()["status"] == "DENIED"


PILOT_ELIG = {
    "seller_type": "PERSON",
    "aspects": {
        k: {"status": st, "note": "verificado na conta do proprietário (piloto)"}
        for k, st in {
            "registration": "SUPPORTED",
            "habitual_sales": "CONDITIONAL",
            "api_access": "SUPPORTED",
            "dropshipping": "CONDITIONAL",
            "shipping": "SUPPORTED",
            "fiscal": "CONDITIONAL",
        }.items()
    },
    "restrictions": ["piloto", "fiscal: aceite de risco do proprietário, sem parecer profissional"],
    "conditions": [
        {"field": "ufs", "allowed": ["PR"], "note": "UF"},
        {"field": "fulfillment_modes", "allowed": ["DROPSHIP_SUPPLIER"], "note": "Dropi"},
    ],
    "pending": ["conta sem reputação"],
    "sources": [{"url": "https://github.com/x/EVIDENCIA_CH-ML.md", "kind": "evidência", "accessed": "2026-10-31"}],
    "scope": "piloto",
}


def test_pilot_path_pf_with_risk_acceptance_allows_publication_proposal(client, auth, monkeypatch):
    """Caminho do piloto: elegibilidade condicional + 6 gates (fiscais por aceite de risco) → proposta em LIVE."""
    import datetime as dt

    from sqlalchemy import update

    from aicommerce.config import RuntimeMode, get_settings
    from aicommerce.models import CertificationGate, ChannelEligibility

    admin, owner = auth("admin"), auth("owner")
    oid = opportunity()  # perfil com UF PR
    with session_scope() as db:
        db.get(SellerProfile, 1).person_type = "PERSON"
    try:
        r = client.put("/v1/seller/eligibility/MERCADO_LIVRE", headers=owner, json=PILOT_ELIG)
        assert r.status_code == 200, r.text
        scope = {"channels": ["MERCADO_LIVRE"], "ufs": ["PR"], "fulfillment_modes": ["DROPSHIP_SUPPLIER"]}
        until = (dt.datetime.now(dt.UTC) + dt.timedelta(days=30)).isoformat()
        for code in ("LEG-01", "TAX-01", "TAX-03", "SUP-01", "CH-ML", "OPS-01"):
            kind = "ACEITE_RISCO" if code in ("LEG-01", "TAX-01", "TAX-03") else "DOCUMENTO"
            ev = client.post(
                f"/v1/gates/{code}/evidence",
                headers=admin,
                json={"kind": kind, "reference": "docs/piloto.md", "notes": "t"},
            )
            assert ev.status_code == 201, (code, ev.text)
            client.post(f"/v1/gates/{code}/submit", headers=admin)
            r = client.post(
                f"/v1/gates/{code}/decide",
                headers=owner,
                json={
                    "decision": "APPROVED_WITH_RESTRICTIONS",
                    "scope": {**scope, "restrictions": ["piloto"]},
                    "valid_until": until,
                    "note": "piloto restrito ao Mercado Livre",
                },
            )
            assert r.status_code == 200, (code, r.text)
        monkeypatch.setattr(get_settings(), "mode", RuntimeMode.LIVE)
        r = client.post(
            f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=auth("operator")
        )
        assert r.status_code == 201 and r.json()["status"] == "PENDING_APPROVAL", r.text
    finally:
        with session_scope() as db:
            db.execute(
                update(CertificationGate).values(
                    state="NOT_STARTED", approved_by=None, valid_until=None, evidence_sha256=None, scope={}
                )
            )
            db.query(ChannelEligibility).delete()
            from aicommerce.governance import seller

            db.flush()
            seller.seed_eligibility(db)


def test_low_profit_is_blocked_before_reaching_the_owner(client, auth):
    """Arranhador a R$ 49,90: tarifa 11,5% + R$ 6,25 fixos + frete do fornecedor → lucro abaixo do mínimo."""
    op = auth("operator")
    oid = opportunity()
    r = client.post(
        f"/v1/opportunities/{oid}/platform-publication",
        json={"price_minor": 4990, "supplier_freight_minor": 1273},
        headers=op,
    )
    assert r.status_code == 409 and "abaixo do mínimo" in r.json()["detail"]
    with session_scope() as db:
        assert db.get(Opportunity, uuid.UUID(oid)).status == "NEW"


def test_economics_shows_fixed_fee_and_buyer_paid_shipping_below_79():
    from aicommerce.commerce import unit_economics

    e = unit_economics.compute(
        price=49.9,
        cost=29.0,
        fee_total=11.99,
        fee_pct=11.5,
        fee_fixed=6.25,
        supplier_freight=12.73,
        seller_shipping=0.0,
        sources={"fee": "f", "shipping": "s", "supplier_freight": "d", "cost": "c"},
    )
    by = {line["key"]: line["amount_minor"] for line in e["lines"]}
    assert by == {
        "price": 4990,
        "fee_pct": -574,
        "fee_fixed": -625,
        "seller_shipping": 0,
        "supplier_freight": -1273,
        "cost": -2900,
    }
    assert e["profit_minor"] == -382  # o mesmo prejuízo que a calculadora do Dropi mostrou (−R$ 3,80)


def test_unknown_supplier_freight_blocks_proposal(client, auth):
    op = auth("operator")
    oid = opportunity()
    with session_scope() as db:
        o = db.get(Opportunity, uuid.UUID(oid))
        src = [dict(s) for s in o.sources]
        src[0]["market"] = {k: v for k, v in src[0]["market"].items() if k != "supplier_freight"}
        o.sources = src
    r = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=op)
    assert r.status_code == 409 and "frete do fornecedor desconhecido" in r.json()["detail"]


def test_competitor_below_our_landed_cost_blocks_proposal(client, auth, monkeypatch):
    """O caso do kit: concorrentes a R$ 36,90 com frete grátis, nosso custo com frete R$ 36,45 + margem → barrado."""
    monkeypatch.setitem(
        MARKET,
        "offers",
        [
            {
                "title": "Mercado Livre - Kit 3 Formas Fundo Removível",
                "url": "https://www.mercadolivre.com.br/x",
                "text": "R$ 36,90 com frete grátis",
            },
            {"title": "Mercado Livre - Kit 3 Formas", "url": "https://www.mercadolivre.com.br/y", "text": "R$ 38,79"},
            {"title": "Shopee - Kit 3 Formas", "url": "https://shopee.com.br/z", "text": "R$ 54,17"},
        ],
    )
    op = auth("operator")
    oid = opportunity()
    r = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 10990}, headers=op)
    assert r.status_code == 409 and "muito acima da mediana" in r.json()["detail"]
    with session_scope() as db:
        o = db.get(Opportunity, uuid.UUID(oid))
        src = [dict(x) for x in o.sources]
        src[0]["market"] = {**src[0]["market"], "supplier_cost": 30.0}
        o.sources = src
    r = client.post(f"/v1/opportunities/{oid}/platform-publication", json={"price_minor": 4990}, headers=op)
    assert r.status_code == 409


def test_market_verdict_rules():
    from aicommerce.ai.tools import market

    chk = {"count": 3, "min": 36.9, "median": 38.79, "max": 54.17}
    ok, why = market.verdict(chk, landed=41.18, price=59.9)
    assert not ok and "abaixo do nosso custo" in why
    ok, why = market.verdict({"count": 1, "min": 50, "median": 50}, landed=20, price=49)
    assert not ok and "sem ofertas suficientes" in why
    ok, why = market.verdict({"count": 4, "min": 60.0, "median": 80.0, "max": 99.0}, landed=41.18, price=79.9)
    assert ok and "mediana R$ 80.00" in why


def test_price_check_also_searches_similar_when_exact_name_has_few_offers(monkeypatch):
    """O caso da cortina: pelo nome exato só 2 anúncios caros; os similares da mesma marca custavam R$ 74–89."""
    from aicommerce.ai import specs
    from aicommerce.ai.tools import market

    def web(q, limit=3, agent_id="luna", prompt=None):
        if "Mercado Livre" in q:
            return [
                {"title": "Mercado Livre - Cortina Blackout Ilhós 2,80m", "url": "https://ml/a", "text": "R$ 73,50"},
                {"title": "Mercado Livre - Cortina Blackout Bege", "url": "https://ml/b", "text": "R$ 84,07"},
            ], None
        return [
            {"title": "Shopee - Cortina Nobre Lar", "url": "https://shopee/x", "text": "R$ 370,03"},
            {"title": "Lojas Sim - Cortina Nobre Lar", "url": "https://sim/y", "text": "R$ 387,99"},
        ], None

    monkeypatch.setattr(specs, "_web_sources", web)
    out = market.price_check({"title": "Cortina Nobre Lar Ilhós 2,70m x 1,80m (CN100625)"})
    assert out["count"] == 4 and out["min"] == 73.5 and " | " in out["query"]
    ok, why = market.verdict(out, landed=194.82, price=378.91)
    assert not ok and "abaixo do nosso custo" in why
