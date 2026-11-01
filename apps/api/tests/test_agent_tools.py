"""Pool de ferramentas: permissão por agente, limite diário, cache, auditoria e cálculos; Mercado Livre falso."""

import datetime as dt

import pytest
from sqlalchemy import delete, select

from aicommerce.ai import tools
from aicommerce.ai.tools import calc, ml
from aicommerce.db import session_scope
from aicommerce.models import ToolCache, ToolCall

NOW = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def clean():
    yield
    with session_scope() as db:
        db.execute(delete(ToolCall))
        db.execute(delete(ToolCache))


@pytest.fixture
def fake_ml(monkeypatch):
    calls = []

    def get(path):
        calls.append(path)
        if path.startswith("/products/search"):
            return {
                "results": [
                    {
                        "id": "MLB1",
                        "name": "Kit 3 Formas",
                        "domain_id": "MLB-CAKE_PANS",
                        "pictures": [{"url": "https://http2.mlstatic.com/a.jpg"}],
                    }
                ]
            }
        if path.startswith("/products/MLB1/items"):
            return {"results": [{"price": 99.9}, {"price": 109.9}, {"price": 129.0}]}
        if path.startswith("/sites/MLB/listing_prices"):
            return [
                {
                    "listing_type_id": "gold_special",
                    "sale_fee_amount": 13.74,
                    "sale_fee_details": {"percentage_fee": 12.5, "fixed_fee": 0},
                }
            ]
        if path == "/users/me":
            return {"id": 123}
        if "/shipping_options/free" in path:
            return {"coverage": {"all_country": {"list_cost": 24.35, "billable_weight": 1200}}}
        raise AssertionError(path)

    monkeypatch.setattr(ml, "_get", get)
    monkeypatch.setattr(ml, "_seller", None)
    return calls


def test_calc_profit_price_and_cpa():
    r = calc.profit({"price": 109.9, "cost": 26.45, "fee_pct": 12.5, "shipping": 24.35})
    assert r["fee"] == 13.74 and r["profit"] == 45.36
    p = calc.price_for_profit({"cost": 29, "fee_pct": 12.5, "fee_fixed": 6.25, "target_profit": 15})
    assert p["check"]["profit"] >= 15 and p["check"]["profit"] < 15.02
    assert (
        calc.max_cpa({"price": 109.9, "cost": 26.45, "fee_pct": 12.5, "shipping": 24.35, "min_profit": 25})[
            "max_ad_cost_per_sale"
        ]
        == 20.36
    )


def test_permission_is_checked_and_every_call_is_logged(fake_ml):
    with session_scope() as db:
        r = tools.call(db, "luna", "ml.match_gtin", {"gtin": "6925073145868"}, now=NOW)
        assert r["found"] and r["products"][0]["sellers"] == 3 and r["products"][0]["price_median"] == 109.9
        with pytest.raises(tools.ToolDenied):
            tools.call(db, "eva", "ml.match_gtin", {"gtin": "6925073145868"}, now=NOW)  # Eva não tem a ferramenta
        assert (
            tools.call(db, "eva", "calc.profit", {"price": 50, "cost": 20}, now=NOW)["profit"] == 30
        )  # cálculo: livre
        rows = db.execute(select(ToolCall.agent_id, ToolCall.tool, ToolCall.status)).all()
    assert ("eva", "ml.match_gtin", "DENIED") in rows and ("luna", "ml.match_gtin", "OK") in rows


def test_cache_avoids_repeating_the_external_call(fake_ml):
    with session_scope() as db:
        a = tools.call(db, "dante", "ml.fees", {"price": 109.9, "category_id": "MLB1234"}, now=NOW)
        b = tools.call(db, "dante", "ml.fees", {"price": 109.9, "category_id": "MLB1234"}, now=NOW)
        assert a == b and a["percentage_fee"] == 12.5
        hits = db.execute(select(ToolCall.cache_hit).where(ToolCall.tool == "ml.fees")).scalars().all()
    assert sorted(hits) == [False, True] and len([c for c in fake_ml if "listing_prices" in c]) == 1
    with session_scope() as db:  # cache vencido: chama de novo
        tools.call(db, "dante", "ml.fees", {"price": 109.9, "category_id": "MLB1234"}, now=NOW + dt.timedelta(days=2))
    assert len([c for c in fake_ml if "listing_prices" in c]) == 2


def test_daily_limit_and_bad_input(fake_ml, monkeypatch):
    t = tools.TOOLS["ml.shipping_cost"]
    monkeypatch.setitem(tools.TOOLS, "ml.shipping_cost", tools.Tool(**{**t.__dict__, "daily_limit": 1, "cache_s": 0}))
    args = {"height": 10, "width": 20, "length": 30, "grams": 1200, "price": 109.9}
    with session_scope() as db:
        assert tools.call(db, "logan", "ml.shipping_cost", args, now=NOW)["seller_cost"] == 24.35
        with pytest.raises(tools.ToolLimit):
            tools.call(db, "logan", "ml.shipping_cost", args, now=NOW)
        with pytest.raises(tools.ToolError):
            tools.call(db, "luna", "ml.match_gtin", {"gtin": "12"}, now=NOW)
        with pytest.raises(tools.ToolError, match="faltam"):
            tools.call(db, "luna", "ml.match_gtin", {}, now=NOW)


def test_ml_without_vault_fails_clearly(monkeypatch):
    from aicommerce.config import get_settings

    monkeypatch.setattr(get_settings(), "ml_readonly_vault", None)
    monkeypatch.setattr(ml, "_reader", None)
    with pytest.raises(tools.ToolError, match="não conectado"):
        ml._get("/users/me")


def test_catalog_api_lists_tools_permissions_and_usage(client, auth, fake_ml):
    with session_scope() as db:
        tools.call(db, "luna", "ml.match_gtin", {"gtin": "6925073145868"})
    items = client.get("/v1/agent-tools?agent_id=luna", headers=auth("operator")).json()["items"]
    by = {i["name"]: i for i in items}
    assert "ml.match_gtin" in by and "calc.profit" in by and "luna" in by["ml.match_gtin"]["agents"]
    assert by["ml.match_gtin"]["usage_30d"]["calls"] == 1 and by["calc.profit"]["always_allowed"]


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>rockets x thunder</title><ht:approx_traffic>200+</ht:approx_traffic><link>https://spam.example/x</link></item>
<item><title>ventilador de teto</title><ht:approx_traffic>5000+</ht:approx_traffic></item>
<item><title>resultado mega-sena</title><ht:approx_traffic>100000+</ht:approx_traffic></item>
<item><title>air fryer &amp; forno</title><ht:approx_traffic>2000+</ht:approx_traffic></item>
</channel></rss>"""


def test_google_daily_keeps_product_like_terms_and_drops_links(monkeypatch):
    import httpx

    from aicommerce.ai.tools import trends

    class Resp:
        status_code, text, content = 200, RSS, RSS.encode()

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            assert url == trends.FEED
            return Resp()

    monkeypatch.setattr(httpx, "Client", Client)
    out = trends.google_daily({})
    assert out == [
        {"term": "ventilador de teto", "traffic": "5000+"},
        {"term": "air fryer & forno", "traffic": "2000+"},
    ]
    assert "spam" not in str(out)


def test_seasonal_flags_what_to_prepare_now():
    from aicommerce.ai.tools import trends

    out = trends.seasonal({"today": "2026-11-01", "horizon_days": 60})
    names = [e["event"] for e in out]
    assert names[0].startswith("Black Friday") and "Natal" in names
    assert out[0]["days_left"] == 26 and out[0]["act_now"] is True


def test_dropi_search_uses_saved_session_and_detects_expiry(tmp_path, monkeypatch):
    import json as _json

    import httpx

    from aicommerce.ai.tools import dropi
    from aicommerce.config import get_settings

    session = tmp_path / "session.json"
    session.write_text(_json.dumps({"cookies": [{"name": "PHPSESSID", "value": "abc", "domain": "app.dropi.com.br"}]}))
    monkeypatch.setattr(get_settings(), "dropi_session_file", str(session))
    seen = {}
    body = {
        "call": {
            "success": {
                "produtos": [
                    {
                        "title": "Kit 3 Formas",
                        "valor": "26.45",
                        "estoque_geral": "10",
                        "fornecedor": "Gringa",
                        "obrigatorio_nf": "2",
                        "imagem": "https://cdn.dropi/x.jpg",
                        "total_pedidos": 7,
                    },
                    {"title": "Sem estoque", "valor": "5", "estoque_geral": "0"},
                ]
            }
        }
    }

    class Resp:
        def __init__(self, status=200, payload=None, headers=None):
            self.status_code, self._p, self.headers = status, payload, headers or {}

        def json(self):
            if self._p is None:
                raise ValueError
            return self._p

    class Client:
        def __init__(self, **kw):
            seen["cookies"] = kw.get("cookies")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, data):
            seen["form"] = data
            return seen.get("resp") or Resp(payload=body)

    monkeypatch.setattr(httpx, "Client", Client)
    out = dropi.search({"term": "forma de bolo"})
    assert (
        [p["title"] for p in out] == ["Kit 3 Formas"]
        and out[0]["cost"] == 26.45
        and out[0]["requires_invoice"] is False
    )
    assert seen["cookies"] == {"PHPSESSID": "abc"} and seen["form"] == {
        "page": "0",
        "search": "forma de bolo",
        "filtro": "",
    }
    seen["resp"] = Resp(status=302, headers={"location": "/login"})
    with pytest.raises(tools.ToolError, match="expirou"):
        dropi.search({"term": "forma"})
    monkeypatch.setattr(get_settings(), "dropi_session_file", None)
    with pytest.raises(tools.ToolError, match="não conectado"):
        dropi.search({"term": "forma"})


def test_cj_tools_map_catalog_freight_and_estimate_import_cost(monkeypatch):
    from aicommerce.ai.tools import cj

    class Fake:
        def products(self, term, country="CN", size=20):
            return [
                {
                    "id": "P1",
                    "nameEn": "Silicone Kitchen Utensil Set",
                    "sellPrice": "4.50-6.00",
                    "bigImage": "https://x/i.jpg",
                },
                {"id": "P2", "nameEn": "Sem preço"},
            ]

        def variants(self, pid):
            return [{"vid": "V1", "variantSellPrice": 0.8}]

        def freight(self, vid, start="CN", end="BR", zip_code=None):
            assert vid == "V1" and end == "BR"
            return [
                {"logisticName": "CJPacket", "logisticPrice": 3.2, "logisticAging": "10-20"},
                {"logisticName": "DHL", "logisticPrice": 25.0, "logisticAging": "5-8"},
            ]

    monkeypatch.setattr(cj, "_client", Fake())
    found = cj.search({"term": "utensílios de silicone"})
    assert found == [
        {
            "pid": "P1",
            "title": "Silicone Kitchen Utensil Set",
            "price_usd": 4.5,
            "image": "https://x/i.jpg",
            "category": None,
        }
    ]
    assert cj.freight({"pid": "P1"})[0] == {"name": "CJPacket", "price_usd": 3.2, "days": "10-20"}
    est = cj.import_landed_brl(4.5, 3.2, 5.40)  # US$ 7,70: II 20% + ICMS 20% por dentro
    assert est["goods_freight_brl"] == 41.58 and est["import_tax_brl"] == round(
        (7.7 * 0.2 + (7.7 * 1.2) / 0.8 * 0.2) * 5.4, 2
    )


def test_ptax_reads_last_quote(monkeypatch):
    import httpx

    from aicommerce.ai.tools import cj

    class R:
        def json(self):
            return {
                "value": [
                    {"cotacaoVenda": 5.31, "dataHoraCotacao": "2026-10-29 13:00"},
                    {"cotacaoVenda": 5.42, "dataHoraCotacao": "2026-10-30 13:00"},
                ]
            }

    monkeypatch.setattr(httpx, "get", lambda url, params=None, timeout=None: R())
    assert cj.usd_brl({}) == {"usd_brl": 5.42, "date": "2026-10-30", "source": "PTAX venda — Banco Central do Brasil"}
