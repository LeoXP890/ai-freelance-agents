"""Caça de produtos (F3): tendência → Dropi → Mercado Livre → conta por venda → oportunidade. Tudo em código.

Zero token de IA: as ferramentas buscam e o código filtra e calcula. Só sobra o que tem relação com o termo em alta,
estoque, não exige nota fiscal (venda como pessoa física), não é regulado/proibido e deixa lucro ≥ mínimo com folga.
O frete do fornecedor ainda não vem da busca do Dropi: entra ESTIMADO (conservador) e precisa ser confirmado antes
da proposta de publicação, que só sai com o valor real (commerce/platform_publish).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from aicommerce.ai import tools
from aicommerce.commerce import unit_economics

AGENT = "luna"
MIN_SELLERS = 3
EST_SUPPLIER_FREIGHT = 15.0  # conservador: fretes do Dropi vistos no piloto foram R$ 12,73 e R$ 14,73
BLOCKED = re.compile(
    r"vibrador|sex|er[oó]tic|beij[aá]vel|excitante|efeito quente|"
    r"lubrificante|preservativo|arma|faca t[aá]tica|simulacro|cigarr|vape|narguil|"
    r"rem[eé]dio|medicament|suplement|whey|creatina|vitamina|cosm[eé]tic|perfume|maquiagem|"
    r"carregador|bateria|power ?bank|fone|eletr[oô]nic|el[eé]tric|bivolt|110 ?v|220 ?v|voltagem|"
    r"tomada|tomada|l[aâ]mpada|led\b|brinquedo|infantil|beb[eê]|"
    r"replica|r[eé]plica|original apple|iphone|samsung|nike|adidas",
    re.I,
)
STOP = {"de", "da", "do", "com", "para", "e", "a", "o", "em", "kit", "un", "cm", "ml"}


def _words(text: str) -> set[str]:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", t) if w not in STOP}


def relevant(term: str, title: str) -> bool:
    """Todas as palavras do termo em alta aparecem no título (singular/plural ignorado)."""
    want = _words(term)
    return bool(want) and want <= _words(title)


def terms(db: Session, *, use_google: bool, limit: int, per_category: int = 4) -> list[dict[str, str]]:
    from aicommerce.ai.ml_market_research import DEFAULT_CATEGORIES

    out: list[dict[str, str]] = []
    # por categoria-alvo primeiro (casa, cozinha, pet…): o geral é dominado por eletrônico e marca
    for cat_id, cat_name in DEFAULT_CATEGORIES.items():
        try:
            found = tools.call(db, AGENT, "trends.ml_searches", {"category_id": cat_id})
        except tools.ToolError:
            continue
        out += [{"term": t, "source": f"em alta no Mercado Livre · {cat_name}"} for t in found[:per_category]]
    try:
        out += [
            {"term": t, "source": "buscas em alta no Mercado Livre"}
            for t in tools.call(db, AGENT, "trends.ml_searches", {})
        ]
    except tools.ToolError:
        pass
    if use_google:
        try:
            out += [
                {"term": g["product"], "source": f"Google ({g['signal'][:60]})"}
                for g in tools.call(db, AGENT, "trends.google_search", {})
            ]
        except tools.ToolError:
            pass
    seen, uniq = set(), []
    for t in out:
        k = " ".join(sorted(_words(t["term"])))
        if k and k not in seen and not BLOCKED.search(t["term"]):
            seen.add(k)
            uniq.append(t)
    return uniq[:limit]


def evaluate(
    db: Session,
    term: dict[str, str],
    item: dict[str, Any],
    *,
    min_profit: float,
    budget: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Mercado Livre (referência opcional) + preço de mercado no Google (obrigatório) + conta. None = descartado."""
    from aicommerce.ai.tools import market

    landed = market.landed_cost(item["cost"], EST_SUPPLIER_FREIGHT)
    ml: dict[str, Any] = {"found": False}
    gtin = re.search(r"\((789\d{10})\)", item["title"])  # EAN-13 brasileiro no título do Dropi: produto idêntico
    if gtin:
        g = tools.call(db, AGENT, "ml.match_gtin", {"gtin": gtin.group(1)})
        if g.get("found") and g["products"][0].get("price_median"):
            ml = {**g["products"][0], "found": True, "similarity": 1.0}
    if not ml.get("found"):
        ml = tools.call(db, AGENT, "ml.match_title", {"title": item["title"]})
    ml_ok = bool(ml.get("found") and ml.get("price_median") and (ml.get("sellers") or 0) >= MIN_SELLERS)
    if ml_ok and float(ml["price_min"]) < landed:
        return None  # já no Mercado Livre há quem venda abaixo do nosso custo com frete: sem chance
    if budget is not None:  # pesquisa de preço no Google tem custo: limite por caça
        if budget.get("price_checks", 0) <= 0:
            return None
        budget["price_checks"] -= 1
    web = tools.call(db, AGENT, "market.price_check", {"title": item["title"]})
    refs = [float(ml["price_median"])] if ml_ok else []
    if web.get("median"):
        refs.append(float(web["median"]))
    if not refs:
        return None
    price = round(min(refs) - 0.1, 2)  # um pouco abaixo da referência mais baixa (ML ou mercado)
    ok, market_note = market.verdict(web, landed=landed, price=price)
    if not ok:
        return None
    cat = tools.call(db, AGENT, "ml.category_predict", {"title": item["title"]})
    if not cat:
        return None
    box = None
    if price >= unit_economics.FREE_SHIPPING_FROM:
        try:
            box = tools.call(db, AGENT, "ml.category_box", {"category_id": cat[0]["category_id"]})
        except tools.ToolError:
            return None
    try:
        econ = unit_economics.for_listing(
            db,
            agent_id=AGENT,
            title=item["title"],
            price=price,
            cost=item["cost"],
            supplier_freight=EST_SUPPLIER_FREIGHT,
            category_id=cat[0]["category_id"],
            box=box,
        )
    except unit_economics.EconomicsError:
        return None
    profit = econ["profit_minor"] / 100
    if profit < min_profit:
        return None
    # termo em alta de uma palavra ("notebook") casa produtos só vizinhos; no modo catálogo o termo é a categoria
    broad = len(_words(term["term"])) < 2 and not term["source"].startswith("Dropi ·")
    score = max(0, min(100, int(30 + min(40, profit) + min(15, web.get("count") or 0) - (15 if broad else 0))))
    brl = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    ml_txt = f"Mercado Livre: {ml.get('sellers')} vendedores (mediana {brl(ml['price_median'])}). " if ml_ok else ""
    rationale = (
        f"Em alta: “{term['term']}” ({term['source']}). Dropi: custo {brl(item['cost'])}, estoque {item['stock']}, "
        f"fornecedor {item.get('supplier') or '—'}. {ml_txt}Mercado (Google): {market_note}. Preço sugerido "
        f"{brl(price)} → sobra ~{brl(profit)} por venda ({econ['margin_pct']}%)."
    )
    return {
        "title": item["title"][:120],
        "category": cat[0].get("category_name") or "Caça Dropi × Mercado Livre",
        "rationale": rationale,
        "contraindications": [
            f"frete do fornecedor estimado em {brl(EST_SUPPLIER_FREIGHT)}: confirmar no Dropi antes da proposta",
            *(["termo em alta genérico (uma palavra): relação com o produto pode ser fraca"] if broad else []),
        ],
        "evidence_quality": "MEDIUM",
        "score": score,
        "sources": [
            {
                "id": f"dropi:{item.get('dropi_id') or item['title'][:40]}",
                "title": f"Dropi · {item['title'][:60]}",
                "url": (ml.get("permalink") if ml_ok else None) or (web["offers"][0]["url"] if web["offers"] else None),
                "date": None,
                "simulated": False,
                "market": {
                    "supplier": item.get("supplier"),
                    "dropi_import_url": item.get("import_url"),
                    "supplier_cost": item["cost"],
                    "stock": item["stock"],
                    "supplier_freight": None,
                    "supplier_freight_estimate": EST_SUPPLIER_FREIGHT,
                    "category_id": econ["category_id"],
                    "suggested_price": price,
                    "economics": econ,
                    "market_check": web,
                    "sellers": ml.get("sellers") if ml_ok else web.get("count"),
                    "price_min": web.get("min"),
                    "price_max": web.get("max"),
                    "price_median": web.get("median"),
                    "ml_product_id": ml.get("product_id") if ml_ok else None,
                    "pictures": [p for p in (item.get("image"), ml.get("picture")) if p],
                    "trend": term,
                    "shipping": {"box": box} if box else {},
                },
            }
        ],
    }


# Modo catálogo: o que já vende no Dropi (mais vistos), varrendo as CATEGORIAS do próprio Dropi; a cada dia começa
# numa página diferente para cobrir o catálogo aos poucos. Fora: eletrônicos/elétricos, moda (tamanho/troca),
# perfumaria, suplementos, sex shop e joias (regulados, alto risco de devolução ou proibidos).
DROPI_CATEGORIES = {
    "80": "Cozinha",
    "83": "Utilidades e organização",
    "110": "Utilidades domésticas",
    "71": "Casa e banho",
    "97": "Cama, mesa e banho",
    "102": "Mesa e decoração",
    "72": "Pet shop",
    "95": "Pesca e camping",
    "78": "Casa (inverno e proteção)",
    "88": "Diversos",
    "92": "Papelaria",
}
SORTS = ("maisvistos30", "maisvistos7")


def catalog_candidates(
    db: Session, *, per_category: int = 6, pages: int = 2, day: int | None = None
) -> list[tuple[dict[str, str], dict[str, Any]]]:
    import datetime as dt

    day = dt.date.today().toordinal() if day is None else day
    out = []
    for n, (cid, name) in enumerate(DROPI_CATEGORIES.items()):
        sort = SORTS[(day + n) % len(SORTS)]
        start = (day // len(SORTS)) % 5 * pages  # rotação diária de páginas (até 10 páginas por categoria)
        try:
            items = tools.call(
                db,
                AGENT,
                "dropi.search",
                {"category_ids": cid, "sort": sort, "start_page": start, "pages": pages, "limit": 80},
            )
        except tools.ToolError as exc:
            if "sessão do Dropi" in str(exc) or "não conectado" in str(exc):
                raise
            continue
        ok = [
            i for i in items if not i["requires_invoice"] and not BLOCKED.search(i["title"]) and 10 <= i["cost"] <= 200
        ]
        ok.sort(key=lambda i: (-int(float(i.get("visits_30d") or 0)), -int(float(i.get("orders_total") or 0))))
        label = "mais vistos no mês" if sort == "maisvistos30" else "mais vistos na semana"
        out += [
            (
                {
                    "term": name,
                    "source": f"Dropi · {name} · {label} ({i.get('visits_30d') or 0} visitas/30 dias, "
                    f"{i.get('orders_total') or 0} pedidos)",
                },
                i,
            )
            for i in ok[:per_category]
        ]
    return out


def hunt(
    db: Session,
    *,
    use_google: bool = False,
    mode: str = "both",
    max_terms: int = 30,
    per_term: int = 6,
    max_checks: int = 50,
    min_profit: float = 20.0,
    max_price_checks: int = 15,
) -> dict[str, Any]:
    """Executa a caça e devolve as oportunidades encontradas (sem gravar) e o resumo do funil."""
    funnel = {"terms": 0, "dropi_items": 0, "relevant": 0, "checked_ml": 0, "kept": 0}
    budget = {"price_checks": max_price_checks}
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    if mode in ("both", "catalog"):
        for t, item in catalog_candidates(db):
            funnel["dropi_items"] += 1
            if len(seen) >= max_checks or item["title"] in seen:
                continue
            seen.add(item["title"])
            funnel["relevant"] += 1
            funnel["checked_ml"] += 1
            try:
                opp = evaluate(db, t, item, min_profit=min_profit, budget=budget)
            except tools.ToolError:
                continue
            if opp:
                found.append(opp)
                funnel["kept"] += 1
    trend_terms = terms(db, use_google=use_google, limit=max_terms) if mode in ("both", "trends") else []
    funnel["terms"] = len(trend_terms)
    for t in trend_terms:
        try:
            items = tools.call(db, AGENT, "dropi.search", {"term": t["term"], "limit": 30})
        except tools.ToolError as exc:
            if "sessão do Dropi" in str(exc) or "não conectado" in str(exc):
                raise
            continue
        funnel["dropi_items"] += len(items)
        for item in items:
            if (
                len(seen) >= max_checks
                or item["title"] in seen
                or item["requires_invoice"]
                or BLOCKED.search(item["title"])
                or not 10 <= item["cost"] <= 200
                or not relevant(t["term"], item["title"])
            ):
                continue
            seen.add(item["title"])
            funnel["relevant"] += 1
            funnel["checked_ml"] += 1
            try:
                opp = evaluate(db, t, item, min_profit=min_profit, budget=budget)
            except tools.ToolError:
                continue
            if opp:
                found.append(opp)
                funnel["kept"] += 1
    found.sort(key=lambda o: -o["score"])
    return {"opportunities": found, "funnel": funnel}


def save(db: Session, task_id: Any, opportunities: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Grava como oportunidades reais da tarefa; a mesma oferta do Dropi ainda aberta é atualizada, não duplicada."""
    from sqlalchemy import select

    from aicommerce.models import Opportunity

    open_rows = {
        s.get("id"): o
        for o in db.execute(select(Opportunity).where(Opportunity.status == "NEW")).scalars()
        for s in (o.sources or [])
        if isinstance(s, dict) and str(s.get("id", "")).startswith("dropi:")
    }
    closed = {  # descartada/rejeitada/publicada antes: decisão tomada, a caça não traz de volta
        s.get("id")
        for o in db.execute(select(Opportunity).where(Opportunity.status != "NEW")).scalars()
        for s in (o.sources or [])
        if isinstance(s, dict)
    }
    created, refreshed = [], []
    for item in opportunities:
        if item["sources"][0]["id"] in closed:
            continue
        existing = open_rows.get(item["sources"][0]["id"])
        if existing is not None:
            existing.rationale, existing.contraindications = item["rationale"], item["contraindications"]
            existing.score, existing.sources = item["score"], item["sources"]
            refreshed.append(str(existing.id))
            continue
        row = Opportunity(
            task_id=task_id,
            is_simulated=False,
            **{
                k: item[k]
                for k in ("title", "category", "rationale", "contraindications", "evidence_quality", "score", "sources")
            },
        )
        db.add(row)
        db.flush()
        created.append(str(row.id))
    return {"opportunity_ids": created, "refreshed_ids": refreshed}


def run_task(task: Any) -> dict[str, Any]:
    """Executor do worker para `research.hunt` (sem IA). Usa a sessão da própria tarefa (evita esperar a trava dela)."""
    from sqlalchemy.orm import object_session

    db = object_session(task)
    inp = task.input or {}
    out = hunt(
        db,
        use_google=bool(inp.get("use_google", False)),
        mode=str(inp.get("mode", "both")),
        max_terms=int(inp.get("max_terms", 30)),
        max_checks=int(inp.get("max_checks", 50)),
        min_profit=float(inp.get("min_profit", 20)),
        max_price_checks=int(inp.get("max_price_checks", 15)),
    )
    saved = save(db, task.id, out["opportunities"])
    top = [
        f"{o['title'][:50]} (~{o['sources'][0]['market']['economics']['profit_minor'] / 100:.2f})"
        for o in out["opportunities"][:5]
    ]
    return {
        "summary": (
            f"Caça: {out['funnel']['terms']} termos em alta → {out['funnel']['dropi_items']} itens no Dropi → "
            f"{out['funnel']['relevant']} relevantes → {out['funnel']['kept']} com lucro. "
            + ("Melhores: " + "; ".join(top) if top else "Nada passou do lucro mínimo hoje.")
        ),
        "funnel": out["funnel"],
        "simulated": False,
        **saved,
    }
