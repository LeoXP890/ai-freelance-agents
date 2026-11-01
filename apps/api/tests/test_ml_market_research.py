"""Pesquisa de mercado ML: triagem explicável, só GET, sem rede (respostas falsas)."""

import uuid

from sqlalchemy import select

from aicommerce.ai import ml_market_research as mr
from aicommerce.db import session_scope
from aicommerce.models import AgentTask, Opportunity


def product(pid, name, domain="MLB-KITCHEN_ORGANIZERS", brand="Genérica", pictures=3):
    return {
        "id": pid,
        "name": name,
        "domain_id": domain,
        "permalink": f"https://www.mercadolivre.com.br/p/{pid}",
        "attributes": [{"id": "BRAND", "value_name": brand}],
        "pictures": [{"url": f"https://http2.mlstatic.com/{pid}-{i}.jpg"} for i in range(pictures)],
    }


def offers(*prices, cat="MLB999"):
    return [{"price": p, "seller_id": i, "category_id": cat} for i, p in enumerate(prices)]


def fake_ml(products, offers_by_pid, trends=("organizador de gaveta",)):
    calls = []

    def get(path):
        calls.append(path)
        if path.startswith("/highlights/"):
            return 200, {
                "content": [{"id": p["id"], "position": i + 1, "type": "PRODUCT"} for i, p in enumerate(products)]
                + [{"id": "MLBX", "position": 99, "type": "ITEM"}]
            }
        if path.startswith("/trends/"):
            return 200, [{"keyword": t} for t in trends]
        if path.startswith("/products/") and path.endswith("?limit=50"):
            pid = path.split("/")[2]
            return 200, {"results": offers_by_pid.get(pid, [])}
        if path.startswith("/products/"):
            pid = path.split("/")[2]
            return next(((200, p) for p in products if p["id"] == pid), (404, {}))
        if path.endswith("/shipping_preferences"):
            return 200, {"dimensions": {"height": 30, "width": 30, "length": 30, "weight": 5000}}
        if "/shipping_options/free" in path:
            return 200, {"coverage": {"all_country": {"list_cost": 39.25, "billable_weight": 7955}}}
        if path.startswith("/sites/MLB/listing_prices"):
            price = float(path.split("price=")[1].split("&")[0])
            return 200, [
                {
                    "listing_type_id": "gold_special",
                    "sale_fee_amount": round(price * 0.125, 2),
                    "sale_fee_details": {"percentage_fee": 12.5, "fixed_fee": 0},
                }
            ]
        return 404, {}

    return get, calls


def test_good_option_scores_high_and_explains_why():
    p = product("MLB1", "Organizador De Gaveta Bambu 6 Divisórias")
    o = mr.evaluate(
        category_name="Casa",
        position=1,
        product=p,
        offers=offers(89.9, 99.9, 109.9),
        fee={"percentage_fee": 12.5, "sale_fee_amount": 12.49},
        trends=["organizador de gaveta"],
    )
    assert o["score"] >= 90
    assert "1º entre os mais vendidos" in o["rationale"] and "mediana R$ 99,90" in o["rationale"]
    assert "busca em alta" in o["rationale"] and "12.5%" in o["rationale"]
    assert o["contraindications"] == [
        "frete por sua conta ainda não calculado (sem medidas ou Mercado Envios inativo)",
        "fornecedor e custo ainda não encontrados: margem real desconhecida",
    ]
    assert len(o["market"]["pictures"]) == 3 and o["market"]["sellers"] == 3


def test_low_ticket_regulated_and_branded_items_are_flagged():
    cheap = mr.evaluate(
        category_name="Casa",
        position=2,
        product=product("MLB2", "Pano"),
        offers=offers(19.9, 21.9),
        fee=None,
        trends=[],
    )
    assert any("abaixo de R$ 79,00" in c for c in cheap["contraindications"])
    toy = mr.evaluate(
        category_name="Casa",
        position=2,
        product=product("MLB3", "Boneco", domain="MLB-TOYS"),
        offers=offers(99.0),
        fee=None,
        trends=[],
    )
    assert any("Inmetro" in c for c in toy["contraindications"])
    branded = mr.evaluate(
        category_name="Casa",
        position=2,
        product=product("MLB4", "Panela", brand="Tramontina"),
        offers=offers(129.0),
        fee=None,
        trends=[],
    )
    assert any("marca Tramontina" in c for c in branded["contraindications"])
    sled = mr.evaluate(
        category_name="Esportes",
        position=3,
        product=product("MLB5", "Trenó", domain="MLB-SLEDS"),
        offers=offers(99.0),
        fee=None,
        trends=[],
    )
    assert not any("Inmetro" in c for c in sled["contraindications"])  # LED não casa com SLEDS
    assert toy["score"] < cheap["score"] < sled["score"]


def test_research_uses_only_get_paths_and_ranks_best_first():
    products = [product("MLB10", "Pano Multiuso"), product("MLB11", "Organizador De Gaveta Bambu")]
    get, calls = fake_ml(products, {"MLB10": offers(19.9, 22.0), "MLB11": offers(89.9, 95.0, 99.0)})
    out = mr.research(get, {"MLB1574": "Casa"}, per_category=5)
    assert [o["title"] for o in out] == ["Organizador De Gaveta Bambu", "Pano Multiuso"]
    src = out[0]["sources"][0]
    assert src["id"] == "ml:product:MLB11" and src["simulated"] is False
    assert src["market"]["fee"]["percentage_fee"] == 12.5 and src["market"]["price_median"] == 95.0
    assert "/products/MLBX" not in " ".join(calls)  # anúncios avulsos (ITEM) não entram como produto
    assert all(c.startswith(("/highlights/", "/trends/", "/products/", "/sites/MLB/listing_prices")) for c in calls)


def test_save_creates_completed_luna_task_and_does_not_duplicate_open_products():
    pid = f"MLB{uuid.uuid4().int % 10**9}"
    get, _ = fake_ml([product(pid, "Organizador De Gaveta")], {pid: offers(89.9, 99.0)})
    items = mr.research(get, {"MLB1574": "Casa"})
    with session_scope() as s:
        first = mr.save(s, items)
    with session_scope() as s:
        again = mr.save(s, mr.research(get, {"MLB1574": "Casa"}))
        task = s.get(AgentTask, uuid.UUID(first["task_id"]))
        rows = s.execute(select(Opportunity).where(Opportunity.task_id == task.id)).scalars().all()
        assert task.agent_id == "luna" and task.state == "COMPLETED"
        assert len(rows) == 1 and rows[0].is_simulated is False and rows[0].sources[0]["market"]["pictures"]
    assert len(first["opportunity_ids"]) == 1 and again["opportunity_ids"] == []
    assert again["refreshed_ids"] == first["opportunity_ids"]  # mesma oportunidade aberta é atualizada, não duplicada


def test_food_containers_are_not_food_but_vet_and_cleaning_products_are_regulated():
    jar = mr.evaluate(
        category_name="Cozinha",
        position=2,
        product=product("MLB6", "Pote", domain="MLB-FOOD_STORAGE_CONTAINERS"),
        offers=offers(99.0),
        fee=None,
        trends=[],
    )
    assert not any("alimento" in c for c in jar["contraindications"])
    for domain, word in (("MLB-NON_PRESCRIPTION_PET_ANTIPARASITICS", "Mapa"), ("MLB-LAUNDRY_DETERGENTS", "Anvisa")):
        o = mr.evaluate(
            category_name="Casa",
            position=1,
            product=product("MLB7", "X", domain=domain),
            offers=offers(99.0),
            fee=None,
            trends=[],
        )
        assert any(word in c for c in o["contraindications"]), domain


def test_package_uses_product_measures_or_category_default():
    p = product("MLB8", "Escorredor")
    p["attributes"] += [
        {"id": "LENGTH", "value_name": "42.5 cm"},
        {"id": "WIDTH", "value_name": "30 cm"},
        {"id": "HEIGHT", "value_name": "365 mm"},
    ]
    default = {"height": 30, "width": 30, "length": 30, "weight": 5000}
    box = mr.package(p, default)
    assert box["cm"] == [39, 32, 45] and box["grams"] == 5000 and "peso padrão" in box["source"]
    p["attributes"].append({"id": "WEIGHT", "value_name": "1,5 kg"})
    assert mr.package(p, default)["grams"] == 1650  # +10% de embalagem
    assert mr.package(product("MLB9", "X"), default)["source"].startswith("padrão da categoria")
    assert mr.package(product("MLB9", "X"), None) is None


def test_shipping_cost_reduces_what_is_left_for_supplier_and_flags_bulky_items():
    products = [product("MLB20", "Escorredor De Louça Inox")]
    get, calls = fake_ml(products, {"MLB20": offers(129.9, 138.65, 149.0)})
    o = mr.research(get, {"MLB1618": "Cozinha"}, seller_id="208563761")[0]
    m = o["sources"][0]["market"]
    assert m["shipping"]["seller_cost"] == 39.25
    assert m["max_supplier_and_tax"] == round(138.65 - round(138.65 * 0.125, 2) - 39.25, 2)
    assert "sua parte ~R$ 39,25" in o["rationale"] and "sobram até" in o["rationale"]
    assert any("/users/208563761/shipping_options/free?" in c for c in calls)
    heavy = mr.evaluate(
        category_name="Casa",
        position=1,
        product=product("MLB21", "Banco"),
        offers=offers(99.0),
        fee={"percentage_fee": 12.5, "sale_fee_amount": 12.38},
        trends=[],
        shipping={"seller_cost": 60.0, "box": {"source": "padrão da categoria"}},
    )
    assert any("mais de 60% do preço" in c for c in heavy["contraindications"])
    no_ship = mr.research(get, {"MLB1618": "Cozinha"})[0]  # sem conta de vendedor: avisa que falta o frete
    assert any("frete por sua conta ainda não calculado" in c for c in no_ship["contraindications"])


def test_shipping_from_unpacked_product_measures_is_flagged_not_penalized():
    o = mr.evaluate(
        category_name="Yoga",
        position=1,
        product=product("MLB30", "Mat De Yoga"),
        offers=offers(147.0),
        fee={"percentage_fee": 11.5, "sale_fee_amount": 16.9},
        trends=[],
        shipping={"seller_cost": 218.45, "box": {"source": "medidas do produto + embalagem"}},
    )
    assert any("confirmar a caixa real" in c for c in o["contraindications"])
    assert not any("mais de 60% do preço" in c for c in o["contraindications"])
