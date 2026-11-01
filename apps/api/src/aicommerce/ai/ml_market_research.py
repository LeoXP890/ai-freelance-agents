"""Pesquisa de mercado real no Mercado Livre (somente GET) para gerar oportunidades com evidência.

Fontes: mais vendidos por categoria (/highlights), tendências de busca (/trends), produto de catálogo (/products: nome,
fotos, atributos) e ofertas concorrentes do mesmo produto (/products/{id}/items), tarifa oficial (/listing_prices).

Isto é lado da demanda: não prova fornecedor, custo, estoque, elegibilidade nem permissão de vender. A nota é uma
triagem determinística e explicável; nenhuma IA decide preço ou compra.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import statistics
import unicodedata
from collections.abc import Callable
from typing import Any

# Categorias de partida sem certificação obrigatória típica no produto inteiro (mesmo assim cada item é triado).
DEFAULT_CATEGORIES = {
    "MLB436414": "Organização para Casa",
    "MLB1618": "Cozinha",
    "MLB1631": "Enfeites e Decoração da Casa",
    "MLB1613": "Banheiros",
    "MLB436246": "Têxteis de Casa e Decoração",
    "MLB1621": "Jardim e Ar Livre",
    "MLB458045": "Recipiente para Ração (Pet)",
    "MLB458037": "Escovas e Pentes (Pet)",
    "MLB85855": "Guias para Animais",
    "MLB278252": "Pilates e Yoga",
    "MLB1338": "Fitness e Musculação",
}
# Domínios com certificação/registro (Anatel, Inmetro, Anvisa, Mapa) ou risco de propriedade intelectual.
REGULATED_DOMAIN_WORDS = {
    "CHARGER": "eletrônico: exige homologação Anatel/Inmetro do fornecedor",
    "ELECTRIC": "elétrico: pode exigir certificação Inmetro",
    "LAMP": "iluminação elétrica: pode exigir certificação Inmetro",
    "LED": "iluminação elétrica: pode exigir certificação Inmetro",
    "TOY": "brinquedo: exige certificação Inmetro",
    "COSMETIC": "cosmético: exige regularização Anvisa",
    "SUPPLEMENT": "suplemento: exige regularização Anvisa",
    "FOOD": "alimento: exige regularização sanitária/Mapa",
    "MEDICINE": "medicamento: proibido sem autorização",
    "HELMET": "equipamento de proteção: exige certificação",
    "CAR_SEAT": "cadeirinha: exige certificação Inmetro",
    "PET_FOOD": "ração: exige registro no Mapa",
    "ANTIPARASITIC": "antiparasitário: produto veterinário com registro no Mapa",
    "VETERINARY": "produto veterinário: exige registro no Mapa",
    "DRUG": "medicamento: proibido sem autorização",
    "DETERGENT": "saneante: exige notificação/registro na Anvisa",
    "DISINFECTANT": "saneante: exige notificação/registro na Anvisa",
    "INSECTICIDE": "saneante/inseticida: exige registro na Anvisa",
    "PESTICIDE": "agrotóxico: exige registro",
    "MATTRESS": "colchão: exige certificação Inmetro",
}
GENERIC_BRANDS = {"generica", "generico", "sem marca", "generic", "nenhuma", "outra", "outros"}
MIN_TICKET = 79.0  # abaixo disso há custo por unidade/frete proporcionalmente alto (ver tarifas oficiais)

Getter = Callable[[str], tuple[int, Any]]


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _product(get: Getter, pid: str) -> dict[str, Any] | None:
    status, data = get(f"/products/{pid}")
    return data if status == 200 and isinstance(data, dict) else None


def _offers(get: Getter, pid: str) -> list[dict[str, Any]]:
    status, data = get(f"/products/{pid}/items?limit=50")
    if status != 200 or not isinstance(data, dict):
        return []
    return [r for r in data.get("results", []) if isinstance(r, dict) and isinstance(r.get("price"), int | float)]


def _fee(get: Getter, price: float, category_id: str) -> dict[str, Any] | None:
    status, data = get(f"/sites/MLB/listing_prices?price={price:.2f}&category_id={category_id}")
    if status != 200 or not isinstance(data, list):
        return None
    for lt in data:
        if lt.get("listing_type_id") == "gold_special":
            d = lt.get("sale_fee_details") or {}
            return {
                "listing_type": "Clássico",
                "sale_fee_amount": lt.get("sale_fee_amount"),
                "percentage_fee": d.get("percentage_fee"),
                "fixed_fee": d.get("fixed_fee"),
            }
    return None


_UNIT = {"mm": 0.1, "cm": 1.0, "m": 100.0, "g": 1.0, "kg": 1000.0}


def _measure(value: str | None) -> tuple[float, str] | None:
    m = re.fullmatch(r"\s*([\d.,]+)\s*(mm|cm|m|g|kg)\s*", value or "")
    return (float(m.group(1).replace(",", ".")) * _UNIT[m.group(2)], m.group(2)) if m else None


def package(product: dict[str, Any], default: dict[str, Any] | None) -> dict[str, Any] | None:
    """Caixa estimada: medidas do produto de catálogo + 2 cm de embalagem; peso do atributo ou padrão da categoria."""
    attrs = {a.get("id"): a.get("value_name") for a in product.get("attributes") or []}
    sides = [_measure(attrs.get(k)) for k in ("HEIGHT", "WIDTH", "LENGTH")]
    weight = next(
        (_measure(attrs.get(k)) for k in ("PACKAGE_WEIGHT", "WEIGHT", "UNIT_WEIGHT") if _measure(attrs.get(k))), None
    )
    if all(x and x[1] in ("mm", "cm", "m") for x in sides):
        dims, source = [math.ceil(round(x[0] + 2, 6)) for x in sides], "medidas do produto + embalagem"
    elif default and all(default.get(k) for k in ("height", "width", "length")):
        dims, source = [int(default[k]) for k in ("height", "width", "length")], "padrão da categoria"
    else:
        return None
    if weight and weight[1] in ("g", "kg"):
        grams = math.ceil(round(weight[0] * 1.1, 6))
    elif default and default.get("weight"):
        grams, source = int(default["weight"]), source + "; peso padrão da categoria"
    else:
        return None
    return {"cm": dims, "grams": grams, "source": source}


def _shipping(get: Getter, seller_id: str, box: dict[str, Any], price: float) -> dict[str, Any] | None:
    height, width, length = box["cm"]
    status, data = get(
        f"/users/{seller_id}/shipping_options/free?dimensions={height}x{width}x{length},{box['grams']}&item_price={price:.2f}"
        "&listing_type_id=gold_special&mode=me2&condition=new&logistic_type=drop_off&verbose=true"
    )
    cov = (data or {}).get("coverage", {}).get("all_country") if status == 200 and isinstance(data, dict) else None
    if not cov or cov.get("list_cost") is None:
        return None
    return {"seller_cost": float(cov["list_cost"]), "billable_weight_g": cov.get("billable_weight"), "box": box}


def _brand(product: dict[str, Any]) -> str | None:
    for a in product.get("attributes") or []:
        if a.get("id") == "BRAND" and a.get("value_name"):
            return str(a["value_name"])
    return None


def evaluate(
    *,
    category_name: str,
    position: int,
    product: dict[str, Any],
    offers: list[dict[str, Any]],
    fee: dict[str, Any] | None,
    trends: list[str],
    min_ticket: float = MIN_TICKET,
    shipping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Nota e justificativa explicáveis a partir dos dados observados (sem fornecedor ainda)."""
    name = str(product.get("name") or product.get("id"))
    prices = sorted(float(o["price"]) for o in offers)
    sellers = len({o.get("seller_id") for o in offers})
    median = statistics.median(prices) if prices else None
    domain = str(product.get("domain_id") or "")
    brand = _brand(product)
    contra: list[str] = []
    why: list[str] = [f"{position}º entre os mais vendidos de {category_name} no Mercado Livre"]
    score = 60 + max(0, 11 - position) * 2

    if prices:
        why.append(
            f"{sellers} vendedor(es) no mesmo produto, de {_brl(prices[0])} a {_brl(prices[-1])} "
            f"(mediana {_brl(median)})"
        )
        if sellers >= 15:
            score -= 10
            contra.append("muita concorrência no mesmo produto de catálogo: disputa por preço")
        elif sellers <= 5:
            score += 5
    else:
        score -= 20
        contra.append("sem ofertas concorrentes visíveis: preço de referência desconhecido")

    if median is not None:
        if median >= min_ticket:
            score += 15
            why.append(f"preço de venda acima de {_brl(min_ticket)}: sobra mais margem por pedido")
        else:
            score -= 15
            contra.append(f"preço típico abaixo de {_brl(min_ticket)}: margem apertada após tarifa e frete")
    if fee and fee.get("percentage_fee") is not None:
        why.append(
            f"tarifa oficial Clássico {fee['percentage_fee']}% ({_brl(float(fee['sale_fee_amount'] or 0))} "
            "no preço mediano)"
        )
    leftover = None
    if median is not None and median >= min_ticket:
        fee_amount = float((fee or {}).get("sale_fee_amount") or 0)
        if shipping:
            leftover = round(median - fee_amount - shipping["seller_cost"], 2)
            why.append(
                f"frete grátis obrigatório: sua parte ~{_brl(shipping['seller_cost'])} "
                f"({shipping['box']['source']}); sobram até {_brl(leftover)} para fornecedor, imposto e lucro"
            )
            if shipping["seller_cost"] > median * 0.5 and shipping["box"]["source"].startswith("medidas do produto"):
                contra.append("frete estimado pelas medidas do produto aberto: confirmar a caixa real com o fornecedor")
            elif leftover < median * 0.4:
                score -= 10
                contra.append("tarifa + frete levam mais de 60% do preço: produto volumoso/pesado para o ticket")
        else:
            contra.append("frete por sua conta ainda não calculado (sem medidas ou Mercado Envios inativo)")
    matched = [t for t in trends if t and all(w in _norm(name) for w in _norm(t).split())]
    if matched:
        score += 10
        why.append(f"coincide com busca em alta: “{matched[0]}”")

    tokens = set(domain.rsplit("-", 1)[-1].split("_"))  # MLB-CAT_AND_DOG_FEEDERS → {CAT, AND, DOG, FEEDERS}
    if tokens & {"CONTAINER", "CONTAINERS", "STORAGE", "POTS", "JARS", "BOWLS", "FEEDERS", "DISPENSERS"}:
        tokens -= {"FOOD"}  # recipiente para alimento não é alimento
    for word, reason in REGULATED_DOMAIN_WORDS.items():
        if word in tokens or f"{word}S" in tokens or f"{word}ES" in tokens or ("_" in word and word in domain):
            score -= 40
            contra.append(reason)
            break
    if brand and _norm(brand) not in GENERIC_BRANDS:
        score -= 20
        contra.append(f"marca {brand}: só vender com fornecedor autorizado/nota de origem")

    contra.append("fornecedor e custo ainda não encontrados: margem real desconhecida")
    pictures = [p["url"] for p in (product.get("pictures") or []) if isinstance(p, dict) and p.get("url")][:4]
    return {
        "title": name[:200],
        "category": f"{category_name} · {domain}"[:120],
        "rationale": "; ".join(why) + ".",
        "contraindications": contra,
        "score": max(0, min(100, score)),
        "evidence_quality": "MEDIUM" if prices else "LOW",
        "market": {
            "product_id": product.get("id"),
            "permalink": product.get("permalink"),
            "domain_id": domain,
            "brand": brand,
            "position": position,
            "sellers": sellers,
            "price_min": prices[0] if prices else None,
            "price_median": median,
            "price_max": prices[-1] if prices else None,
            "category_id": next((o.get("category_id") for o in offers if o.get("category_id")), None),
            "fee": fee,
            "pictures": pictures,
            "trend_match": matched[0] if matched else None,
            "shipping": shipping,
            "max_supplier_and_tax": leftover,
        },
    }


def research(
    get: Getter,
    categories: dict[str, str] | None = None,
    *,
    per_category: int = 8,
    min_ticket: float = MIN_TICKET,
    now: dt.datetime | None = None,
    seller_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retorna oportunidades avaliadas (melhores primeiro). `get(path) -> (status, json)` faz só GET."""
    observed = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    out: list[dict[str, Any]] = []
    for cat_id, cat_name in (categories or DEFAULT_CATEGORIES).items():
        status, hl = get(f"/highlights/MLB/category/{cat_id}")
        if status != 200 or not isinstance(hl, dict):
            continue
        status, tr = get(f"/trends/MLB/{cat_id}")
        trends = [t.get("keyword", "") for t in tr] if status == 200 and isinstance(tr, list) else []
        entries = [c for c in hl.get("content", []) if c.get("type") == "PRODUCT"][:per_category]
        for entry in entries:
            product = _product(get, entry["id"])
            if product is None:
                continue
            offers = _offers(get, entry["id"])
            prices = sorted(float(o["price"]) for o in offers)
            ref_category = next((o.get("category_id") for o in offers if o.get("category_id")), None)
            fee = _fee(get, statistics.median(prices), ref_category) if prices and ref_category else None
            shipping = None
            if seller_id and prices and ref_category and statistics.median(prices) >= min_ticket:
                st, pref = get(f"/categories/{ref_category}/shipping_preferences")
                box = package(product, (pref or {}).get("dimensions") if st == 200 and isinstance(pref, dict) else None)
                shipping = _shipping(get, seller_id, box, statistics.median(prices)) if box else None
            item = evaluate(
                category_name=cat_name,
                position=int(entry.get("position") or 99),
                product=product,
                offers=offers,
                fee=fee,
                trends=trends,
                min_ticket=min_ticket,
                shipping=shipping,
            )
            item["sources"] = [
                {
                    "id": f"ml:product:{product.get('id')}",
                    "title": f"Mercado Livre — {item['title']}",
                    "url": product.get("permalink") or f"https://www.mercadolivre.com.br/p/{product.get('id')}",
                    "date": observed,
                    "simulated": False,
                    "market": item.pop("market"),
                },
                {
                    "id": f"ml:highlights:{cat_id}",
                    "title": f"Mais vendidos — {cat_name}",
                    "url": f"https://www.mercadolivre.com.br/mais-vendidos/{cat_id}",
                    "date": observed,
                    "simulated": False,
                },
            ]
            out.append(item)
    return sorted(out, key=lambda o: o["score"], reverse=True)


def save(db, items: list[dict[str, Any]], *, actor=None, categories: dict[str, str] | None = None) -> dict[str, Any]:
    """Grava como tarefa concluída da Luna + oportunidades reais (não simuladas). Produto já aberto é atualizado."""
    from sqlalchemy import select

    from aicommerce.models import Opportunity
    from aicommerce.workforce import tasks

    open_rows = {
        s.get("id"): o
        for o in db.execute(select(Opportunity).where(Opportunity.status == "NEW")).scalars()
        for s in (o.sources or [])
        if isinstance(s, dict) and str(s.get("id", "")).startswith("ml:product:")
    }
    task = tasks.create(
        db,
        agent_id="luna",
        kind="research.ml_market",
        input_={"source": "mercadolivre", "categories": categories or DEFAULT_CATEGORIES},
        actor=actor,
    )
    if task.state == tasks.TaskState.CREATED:
        tasks.transition(db, task, tasks.TaskState.QUEUED)
    tasks.transition(db, task, tasks.TaskState.RUNNING)
    created, refreshed = [], []
    for item in items:
        existing = open_rows.get(item["sources"][0]["id"])
        if existing is not None:  # mesma oportunidade ainda aberta: atualiza com os dados novos, sem duplicar
            existing.rationale, existing.contraindications = item["rationale"], item["contraindications"]
            existing.score, existing.sources = item["score"], item["sources"]
            existing.evidence_quality, existing.title = item["evidence_quality"], item["title"]
            refreshed.append(str(existing.id))
            continue
        row = Opportunity(
            task_id=task.id,
            title=item["title"],
            category=item["category"],
            rationale=item["rationale"],
            contraindications=item["contraindications"],
            evidence_quality=item["evidence_quality"],
            score=item["score"],
            sources=item["sources"],
            is_simulated=False,
        )
        db.add(row)
        db.flush()
        created.append(str(row.id))
    result = {"opportunity_ids": created, "refreshed_ids": refreshed, "source": "mercadolivre (GET)"}
    tasks.transition(db, task, tasks.TaskState.COMPLETED, result=result)
    return {"task_id": str(task.id), **result}
