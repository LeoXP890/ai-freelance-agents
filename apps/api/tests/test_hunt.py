"""Caça de produtos: em alta → Dropi → Mercado Livre → conta; filtra irrelevante, proibido, com nota e sem lucro."""

import pytest
from sqlalchemy import delete, select

from aicommerce.ai import hunt
from aicommerce.ai.tools import dropi, ml
from aicommerce.db import session_scope
from aicommerce.models import AgentTask, Opportunity, ToolCache, ToolCall
from aicommerce.workforce import tasks

ITEMS = [
    {
        "title": "Pote Hermético Vidro 1L",
        "valor": "15.00",
        "estoque_geral": "50",
        "fornecedor": "Casa Ltda",
        "obrigatorio_nf": "2",
        "id_produto_fornecedor": "A1",
    },
    {"title": "Pote Hermético Vidro com NF", "valor": "15.00", "estoque_geral": "50", "obrigatorio_nf": "1"},
    {"title": "Vibrador pote hermetico", "valor": "15.00", "estoque_geral": "50", "obrigatorio_nf": "2"},
    {"title": "Saco Ziplock", "valor": "15.00", "estoque_geral": "50", "obrigatorio_nf": "2"},
    {
        "title": "Pote Hermético Vidro Caro",
        "valor": "60.00",
        "estoque_geral": "50",
        "obrigatorio_nf": "2",
        "id_produto_fornecedor": "A2",
    },
]


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    def get(path):
        if path.startswith("/trends/MLB"):
            return [{"keyword": "pote hermetico"}, {"keyword": "pote hermético"}]
        if path.startswith("/products/search"):
            return {"results": [{"id": "MLB9", "name": "Pote Hermético de Vidro 1 Litro", "pictures": []}]}
        if path.startswith("/products/MLB9/items"):
            return {"results": [{"price": 69.9}, {"price": 79.9}, {"price": 89.9}]}
        if path.startswith("/sites/MLB/domain_discovery"):
            return [{"category_id": "MLB1", "category_name": "Potes", "domain_name": "Potes"}]
        if path.startswith("/categories/MLB1/shipping_preferences"):
            return {"dimensions": {"height": 10, "width": 15, "length": 15, "weight": 800}}
        if path.startswith("/sites/MLB/listing_prices"):
            price = float(path.split("price=")[1].split("&")[0])
            return [
                {
                    "listing_type_id": "gold_special",
                    "sale_fee_amount": round(price * 0.115, 2),
                    "sale_fee_details": {"percentage_fee": 11.5, "fixed_fee": 0},
                }
            ]
        if path == "/users/me":
            return {"id": 1}
        if "/shipping_options/free" in path:
            return {"coverage": {"all_country": {"list_cost": 20.0}}}
        raise AssertionError(path)

    monkeypatch.setattr(ml, "_get", get)
    monkeypatch.setattr(ml, "_seller", None)
    monkeypatch.setattr(dropi, "_post", lambda path, form: {"call": {"success": {"produtos": ITEMS}}})
    from aicommerce.ai import specs

    web = [
        {
            "title": "Mercado Livre - Pote Hermético",
            "url": "https://www.mercadolivre.com.br/a",
            "text": "R$ 79,90 com frete grátis",
        },
        {"title": "Shopee - Pote Hermético", "url": "https://shopee.com.br/b", "text": "R$ 84,90"},
    ]
    cheap = [  # ziplock: concorrentes abaixo do nosso custo com frete → descartado
        {"title": "Mercado Livre - Saco Ziplock", "url": "https://www.mercadolivre.com.br/z", "text": "R$ 9,90"},
        {"title": "Shopee - Saco Ziplock", "url": "https://shopee.com.br/z", "text": "R$ 12,90"},
    ]
    monkeypatch.setattr(
        specs, "_web_sources", lambda q, limit=3, agent_id="luna", prompt=None: (cheap if "Saco" in q else web, None)
    )
    yield
    with session_scope() as db:
        db.execute(delete(ToolCall))
        db.execute(delete(ToolCache))


def test_electric_appliances_are_blocked_as_regulated():
    assert hunt.BLOCKED.search("Máquina Elétrica De Mini Waffles Bivolt")
    assert hunt.BLOCKED.search("Liquidificador 220V")
    assert not hunt.BLOCKED.search("Pote Hermético de Vidro")


def test_relevance_requires_all_trend_words():
    assert hunt.relevant("pote hermetico", "Pote Hermético Vidro 1L")
    assert hunt.relevant("potes herméticos", "Pote Hermético Vidro")
    assert not hunt.relevant("pote hermetico", "Saco Ziplock")


def test_hunt_keeps_only_relevant_allowed_profitable_items():
    with session_scope() as db:
        out = hunt.hunt(db, mode="trends", min_profit=20)
    titles = [o["title"] for o in out["opportunities"]]
    assert titles == ["Pote Hermético Vidro 1L"]  # com NF, proibido, irrelevante e sem lucro ficam fora
    assert out["funnel"]["terms"] == 1  # "pote hermetico" e "pote hermético" são o mesmo termo
    m = out["opportunities"][0]["sources"][0]["market"]
    assert m["suggested_price"] == 79.8 and m["supplier_freight"] is None and m["supplier_freight_estimate"] == 15.0
    assert m["economics"]["profit_minor"] == 7980 - 918 - 2000 - 1500 - 1500
    assert "mediana R$ 79,90" in out["opportunities"][0]["rationale"]


def test_worker_runs_hunt_task_and_saves_without_duplicates():
    from aicommerce import worker

    for _ in range(2):
        with session_scope() as db:
            t = tasks.create(
                db, agent_id="luna", kind="research.hunt", input_={"min_profit": 20, "mode": "trends"}, actor=None
            )
            tid = t.id
        while worker.run_once():
            pass
        with session_scope() as db:
            t = db.get(AgentTask, tid)
            assert t.state == "COMPLETED", t.error
            assert "1 com lucro" in t.result["summary"]
    with session_scope() as db:
        rows = db.execute(select(Opportunity).where(Opportunity.title == "Pote Hermético Vidro 1L")).scalars().all()
        assert len(rows) == 1 and rows[0].is_simulated is False
        db.delete(rows[0])


def test_catalog_mode_starts_from_what_sells_in_dropi():
    with session_scope() as db:
        out = hunt.hunt(db, mode="catalog", min_profit=20)
    titles = sorted({o["title"] for o in out["opportunities"]})
    # sem NF, permitido e com lucro; o caro dá prejuízo e o ziplock tem concorrente abaixo do nosso custo
    assert titles == ["Pote Hermético Vidro 1L"]
    assert all("Dropi · " in o["rationale"] for o in out["opportunities"])


def test_catalog_rotates_sort_and_pages_by_day(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dropi, "_post", lambda path, form: calls.append(form) or {"call": {"success": {"produtos": []}}}
    )
    with session_scope() as db:
        hunt.catalog_candidates(db, day=0)
        hunt.catalog_candidates(db, day=2)
    first, later = calls[0], calls[len(calls) // 2]
    assert first["categoria[]"] == ["80"] and first["filtro"] == "maisvistos30" and first["page"] == "0"
    assert later["page"] == "2"  # outro dia, outras páginas do catálogo


def test_discarded_opportunity_never_comes_back():
    from aicommerce.workforce import tasks as t

    with session_scope() as db:
        out = hunt.hunt(db, mode="trends", min_profit=20)
        task = t.create(db, agent_id="luna", kind="research.hunt", input_={}, actor=None)
        first = hunt.save(db, task.id, out["opportunities"])
        row = db.get(Opportunity, __import__("uuid").UUID(first["opportunity_ids"][0]))
        row.status = "DISCARDED"
        db.flush()
        again = hunt.save(db, task.id, out["opportunities"])
        assert again == {"opportunity_ids": [], "refreshed_ids": []}
        db.delete(row)
