"""Ferramentas de leitura do Mercado Livre (conta do proprietário, OAuth somente leitura, lista fechada de GETs).

O token fica no cofre privado (AIC_ML_READONLY_VAULT, 0600) e nunca sai do leitor. Respostas são resumidas aqui
(só os campos úteis) antes de chegar ao agente: menos tokens e nada de dado pessoal de comprador.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aicommerce.ai.tools import Tool, ToolError, register
from aicommerce.config import get_settings

DAY = 86_400
_reader: Any = None
_seller: str | None = None


def _get(path: str) -> Any:
    global _reader
    vault = get_settings().ml_readonly_vault
    if not vault:
        raise ToolError("Mercado Livre não conectado neste servidor (AIC_ML_READONLY_VAULT)")
    from aicommerce.providers.ml_onboarding import OnboardingError, ReadOnlyOnboarding

    for attempt in (1, 2):
        if _reader is None:
            try:
                _reader = ReadOnlyOnboarding(Path(vault)).research_reader(authorized=True, refresh_if_needed=True)
            except OnboardingError as exc:
                raise ToolError(f"acesso ao Mercado Livre indisponível ({exc})") from None
        r = _reader.get(path)
        if r.status == "OK":
            return r.data
        if r.status == "REAUTH_REQUIRED" and attempt == 1:
            _reader = None  # token venceu entre chamadas: renova uma vez
            continue
        raise ToolError(f"Mercado Livre respondeu {r.status} (HTTP {r.http})")
    raise ToolError("Mercado Livre indisponível")


def seller_id() -> str:
    global _seller
    if _seller is None:
        _seller = str(_get("/users/me")["id"])
    return _seller


def _offers(pid: str) -> dict[str, Any]:
    try:
        data = _get(f"/products/{pid}/items?limit=50")
    except ToolError as exc:
        if "HTTP 404" in str(exc):  # produto de catálogo sem nenhuma oferta ativa
            return {"sellers": 0, "price_min": None, "price_median": None, "price_max": None}
        raise
    prices = sorted(float(r["price"]) for r in data.get("results", []) if isinstance(r.get("price"), int | float))
    return {
        "sellers": len(prices),
        "price_min": prices[0] if prices else None,
        "price_median": round(statistics.median(prices), 2) if prices else None,
        "price_max": prices[-1] if prices else None,
    }


def match_gtin(args: dict[str, Any]) -> dict[str, Any]:
    gtin = "".join(ch for ch in str(args["gtin"]) if ch.isdigit())
    if not 8 <= len(gtin) <= 14:
        raise ToolError("GTIN precisa ter de 8 a 14 dígitos")
    found = _get(f"/products/search?site_id=MLB&status=active&product_identifier={gtin}")
    out = []
    for p in (found.get("results") or [])[:2]:
        pics = p.get("pictures") or []
        out.append(
            {
                "product_id": p.get("id"),
                "name": p.get("name"),
                "domain_id": p.get("domain_id"),
                "permalink": f"https://www.mercadolivre.com.br/p/{p.get('id')}",
                "picture": pics[0].get("url") if pics and isinstance(pics[0], dict) else None,
                **_offers(str(p.get("id"))),
            }
        )
    return {"gtin": gtin, "found": bool(out), "products": out}


def fees(args: dict[str, Any]) -> dict[str, Any]:
    price, cat = float(args["price"]), str(args["category_id"])
    if not cat.startswith("MLB") or price <= 0:
        raise ToolError("categoria MLB… e preço positivo")
    for lt in _get(f"/sites/MLB/listing_prices?price={price:.2f}&category_id={cat}"):
        if lt.get("listing_type_id") == str(args.get("listing_type_id", "gold_special")):
            d = lt.get("sale_fee_details") or {}
            return {
                "listing_type_id": lt["listing_type_id"],
                "sale_fee": lt.get("sale_fee_amount"),
                "percentage_fee": d.get("percentage_fee"),
                "fixed_fee": d.get("fixed_fee"),
            }
    raise ToolError("tipo de anúncio não encontrado para a categoria")


def shipping_cost(args: dict[str, Any]) -> dict[str, Any]:
    dims = [round(float(args[k])) for k in ("height", "width", "length")]
    grams, price = round(float(args["grams"])), float(args["price"])
    if min(dims) <= 0 or grams <= 0:
        raise ToolError("medidas e peso positivos")
    data = _get(
        f"/users/{seller_id()}/shipping_options/free?dimensions={dims[0]}x{dims[1]}x{dims[2]},{grams}"
        f"&item_price={price:.2f}&listing_type_id=gold_special&mode=me2&condition=new&logistic_type=drop_off&verbose=true"
    )
    cov = (data.get("coverage") or {}).get("all_country") or {}
    if cov.get("list_cost") is None:
        raise ToolError("Mercado Livre não devolveu custo de frete")
    return {
        "seller_cost": float(cov["list_cost"]),
        "billable_weight_g": cov.get("billable_weight"),
        "note": "abaixo de R$ 79 o frete grátis não é obrigatório: o comprador pode pagar",
    }


def category_predict(args: dict[str, Any]) -> list[dict[str, Any]]:
    q = str(args["title"]).strip()[:120]
    if len(q) < 3:
        raise ToolError("título curto demais")
    data = _get(f"/sites/MLB/domain_discovery/search?q={quote(q)}&limit=3")
    return [
        {
            "category_id": d.get("category_id"),
            "category_name": d.get("category_name"),
            "domain_name": d.get("domain_name"),
        }
        for d in data or []
    ]


def category_attributes(args: dict[str, Any]) -> list[dict[str, Any]]:
    cat = str(args["category_id"])
    if not cat.startswith("MLB"):
        raise ToolError("categoria MLB…")
    out = []
    for a in _get(f"/categories/{cat}/attributes"):
        tags = a.get("tags") or {}
        if tags.get("required") or tags.get("catalog_required") or tags.get("conditional_required"):
            vals = [v.get("name") for v in (a.get("values") or [])[:8]]
            out.append(
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "required": bool(tags.get("required")),
                    "values": vals or None,
                }
            )
    return out


def trends(args: dict[str, Any]) -> list[str]:
    cat = str(args.get("category_id") or "")
    if cat and not cat.startswith("MLB"):
        raise ToolError("categoria MLB…")
    data = _get(f"/trends/MLB/{cat}" if cat else "/trends/MLB")
    return [str(t.get("keyword")) for t in data or [] if t.get("keyword")][:50]


def bestsellers(args: dict[str, Any]) -> list[dict[str, Any]]:
    cat = str(args["category_id"])
    if not cat.startswith("MLB"):
        raise ToolError("categoria MLB…")
    data = _get(f"/highlights/MLB/category/{cat}")
    return [
        {"position": c.get("position"), "id": c.get("id"), "type": c.get("type")}
        for c in (data.get("content") or [])[:20]
    ]


SRC = "API oficial do Mercado Livre (leitura)"
register(
    Tool(
        "ml.match_gtin",
        "Mesmo produto no Mercado Livre",
        "Pelo código de barras: produto de catálogo, vendedores e preços (mín./mediana/máx.).",
        {"gtin": "código de barras (EAN/GTIN)"},
        match_gtin,
        cache_s=6 * 3600,
        daily_limit=400,
        required=("gtin",),
        source=SRC,
    )
)
register(
    Tool(
        "ml.fees",
        "Tarifa do Mercado Livre",
        "Tarifa oficial de venda por preço e categoria (Clássico por padrão).",
        {"price": "preço (R$)", "category_id": "categoria MLB…", "listing_type_id": "gold_special ou gold_pro"},
        fees,
        cache_s=DAY,
        required=("price", "category_id"),
        source=SRC,
    )
)
register(
    Tool(
        "ml.shipping_cost",
        "Frete por conta do vendedor",
        "Custo do frete grátis (Mercado Envios) para a caixa e o preço.",
        {"height": "cm", "width": "cm", "length": "cm", "grams": "peso com embalagem (g)", "price": "preço (R$)"},
        shipping_cost,
        cache_s=DAY,
        required=("height", "width", "length", "grams", "price"),
        source=SRC,
    )
)
register(
    Tool(
        "ml.category_predict",
        "Categoria pelo título",
        "Categoria do Mercado Livre mais provável para o título.",
        {"title": "título do produto"},
        category_predict,
        cache_s=7 * DAY,
        required=("title",),
        source=SRC,
    )
)
register(
    Tool(
        "ml.category_attributes",
        "Atributos obrigatórios",
        "Atributos exigidos pela categoria (evita anúncio recusado).",
        {"category_id": "categoria MLB…"},
        category_attributes,
        cache_s=7 * DAY,
        required=("category_id",),
        source=SRC,
    )
)
register(
    Tool(
        "trends.ml_searches",
        "Buscas em alta no Mercado Livre",
        "O que os brasileiros mais buscam para comprar (geral ou por categoria).",
        {"category_id": "categoria MLB… (opcional)"},
        trends,
        cache_s=6 * 3600,
        source=SRC,
    )
)
register(
    Tool(
        "trends.ml_bestsellers",
        "Mais vendidos no Mercado Livre",
        "Ranking dos mais vendidos da categoria.",
        {"category_id": "categoria MLB…"},
        bestsellers,
        cache_s=6 * 3600,
        required=("category_id",),
        source=SRC,
    )
)


def _tokens(text: str) -> set[str]:
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    stop = {"de", "da", "do", "com", "para", "e", "a", "o", "em", "kit", "un", "unidades", "cm", "ml", "pcs", "x"}
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", t) if w not in stop}


def match_title(args: dict[str, Any]) -> dict[str, Any]:
    """Produto de catálogo mais parecido pelo nome + vendedores/preços; similaridade por palavras em comum."""
    import re

    title = re.sub(r"\([^)]*\)|\bCor:\s*\w+", " ", str(args["title"])).strip()[:120]  # tira SKU/código e cor
    want = _tokens(title)
    if len(want) < 2:
        raise ToolError("título sem palavras suficientes para comparar")
    try:
        found = _get(f"/products/search?site_id=MLB&status=active&q={quote(title[:80])}&limit=10")
    except ToolError as exc:
        if "HTTP 404" in str(exc):  # o Mercado Livre responde 404 quando a busca não acha nada
            return {"found": False, "similarity": 0.0}
        raise
    best, score = None, 0.0
    for p in found.get("results") or []:
        have = _tokens(str(p.get("name") or ""))
        s = len(want & have) / len(want | have) if have else 0.0
        if s > score:
            best, score = p, s
    if best is None or score < float(args.get("min_similarity", 0.3)):
        return {"found": False, "similarity": round(score, 2)}
    pics = best.get("pictures") or []
    return {
        "found": True,
        "similarity": round(score, 2),
        "product_id": best.get("id"),
        "name": best.get("name"),
        "domain_id": best.get("domain_id"),
        "permalink": f"https://www.mercadolivre.com.br/p/{best.get('id')}",
        "picture": pics[0].get("url") if pics and isinstance(pics[0], dict) else None,
        **_offers(str(best.get("id"))),
    }


def category_box(args: dict[str, Any]) -> dict[str, Any]:
    cat = str(args["category_id"])
    if not cat.startswith("MLB"):
        raise ToolError("categoria MLB…")
    d = (_get(f"/categories/{cat}/shipping_preferences") or {}).get("dimensions") or {}
    if not d.get("weight"):
        raise ToolError("categoria sem medidas padrão")
    return {
        "cm": [int(d["height"]), int(d["width"]), int(d["length"])],
        "grams": int(d["weight"]),
        "source": "medida padrão da categoria no Mercado Livre",
    }


register(
    Tool(
        "ml.match_title",
        "Produto parecido no Mercado Livre",
        "Produto de catálogo mais parecido pelo nome: similaridade, vendedores e preços.",
        {"title": "nome do produto", "min_similarity": "0 a 1 (padrão 0,3)"},
        match_title,
        cache_s=12 * 3600,
        daily_limit=600,
        required=("title",),
        source=SRC,
    )
)
register(
    Tool(
        "ml.category_box",
        "Caixa padrão da categoria",
        "Medidas e peso padrão da categoria para cotar frete quando o produto não tem medidas.",
        {"category_id": "categoria MLB…"},
        category_box,
        cache_s=7 * DAY,
        required=("category_id",),
        source=SRC,
    )
)


def my_listing_find(args: dict[str, Any]) -> dict[str, Any]:
    """Procura nos MEUS anúncios o que tem o título publicado (o robô confere preço e status depois de publicar)."""
    title = str(args["title"]).strip()[:60]
    data = _get(f"/users/{seller_id()}/items/search?q={quote(title)}&limit=20")
    want = _tokens(title)
    best = None
    for item_id in (data.get("results") or [])[:10]:
        it = _get(f"/items/{item_id}")
        have = _tokens(str(it.get("title") or ""))
        sim = len(want & have) / len(want | have) if have else 0.0
        if sim >= 0.6 and (best is None or sim > best["similarity"]):
            best = {
                "listing_id": it.get("id"),
                "title": it.get("title"),
                "price": it.get("price"),
                "status": it.get("status"),
                "permalink": it.get("permalink"),
                "similarity": round(sim, 2),
            }
    return {"found": best is not None, **(best or {})}


register(
    Tool(
        "ml.my_listing_find",
        "Achar meu anúncio",
        "Procura nos anúncios da conta o anúncio com o título dado (status, preço, link).",
        {"title": "título do anúncio"},
        my_listing_find,
        daily_limit=200,
        required=("title",),
        source=SRC,
    )
)
