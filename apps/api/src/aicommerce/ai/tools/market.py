"""Preço de mercado pelo Google (Gemini com Google Search): ofertas atuais do mesmo tipo de produto, com fonte.

Usado na caça e OBRIGATÓRIO antes de propor publicação: se concorrente vende abaixo do nosso custo com frete, o
produto é descartado; o preço sugerido segue o mercado. Só preços que aparecem em páginas encontradas na pesquisa
(domínio conferido com o Google) entram; nada é inventado.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from aicommerce.ai.tools import Tool, ToolError, register

PRICE_PROMPT = (
    "Pesquise na web (Brasil) e liste até {limit} OFERTAS à venda agora para: {query}\n"
    "Prefira Mercado Livre, Shopee, Amazon e Magazine Luiza. Use a ferramenta de busca do Google. Responda com linhas "
    "no formato: LOJA - PRODUTO | URL https completa | PREÇO em R$ e se o frete é grátis (até 15 palavras).\n"
    "Não invente URLs nem preços: use apenas páginas encontradas na pesquisa."
)
_PRICE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:,\d{2})?)")


def _brl(text: str) -> float | None:
    m = _PRICE.search(text or "")
    if not m:
        return None
    v = float(m.group(1).replace(".", "").replace(",", "."))
    return v if 1 <= v <= 50_000 else None


def _offers(query: str, limit: int) -> list[dict[str, Any]]:
    from aicommerce.ai.specs import _web_sources

    sources, note = _web_sources(query, limit=limit, prompt=PRICE_PROMPT)
    if not sources and note:
        raise ToolError(note)
    out = []
    for s in sources:
        price = _brl(s.get("text", ""))
        if price is None:
            continue
        out.append(
            {
                "store": (s.get("title") or "").split(" - ")[0].strip()[:40],
                "product": (s.get("title") or "")[:90],
                "price": price,
                "free_shipping": "grátis" in (s.get("text") or "").lower(),
                "url": s.get("url"),
            }
        )
    return out


def generic(title: str) -> str:
    """Tipo de produto + medidas, sem marca/linha/código: acha os SIMILARES que o comprador vai comparar."""
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}|\d+(?:[.,]\d+)?\s?(?:m|cm|mm|ml|l|kg|g)\b", title)
    return " ".join(words[:2] + [w for w in words if re.match(r"\d", w)][:2])


def price_check(args: dict[str, Any]) -> dict[str, Any]:
    title = re.sub(r"\([^)]*\)|\bCor:\s*\w+", " ", str(args["title"])).strip()[:100]
    if len(title) < 5:
        raise ToolError("nome do produto curto demais")
    limit = int(args.get("limit", 8))
    offers = _offers(f"{title} preço", limit)
    queries = [title]
    if len(offers) < 4:  # poucas ofertas pelo nome exato (caso da cortina): procura os similares também
        g = generic(title)
        if g and g.lower() != title.lower():
            queries.append(g)
            seen = {o["url"] for o in offers}
            offers += [o for o in _offers(f"{g} preço Mercado Livre", limit) if o["url"] not in seen]
    prices = sorted(o["price"] for o in offers)
    return {
        "query": " | ".join(queries),
        "offers": sorted(offers, key=lambda o: o["price"]),
        "count": len(prices),
        "min": prices[0] if prices else None,
        "median": round(statistics.median(prices), 2) if prices else None,
        "max": prices[-1] if prices else None,
    }


register(
    Tool(
        "market.price_check",
        "Preço de mercado (Google)",
        "Ofertas atuais do produto em lojas (Mercado Livre, Shopee, Amazon, Magalu) com preço, frete grátis e link.",
        {"title": "nome do produto", "limit": "ofertas (padrão 8)"},
        price_check,
        cost_micro_usd=3_000,
        cache_s=24 * 3600,
        version=2,  # 01/11: busca também similares quando o nome exato tem poucas ofertas
        daily_limit=100,
        required=("title",),
        source="Google Search via Gemini (custo no teto de IA)",
    )
)


def landed_cost(cost: float, supplier_freight: float) -> float:
    return round(cost + supplier_freight, 2)


def verdict(check: dict[str, Any], *, landed: float, price: float) -> tuple[bool, str]:
    """Regra única (caça e proposta): concorrente abaixo do nosso custo com frete → descarta; preço muito acima da
    mediana → descarta (não vende). Sem ofertas suficientes, não afirma nada: precisa de pelo menos 2."""
    if (check.get("count") or 0) < 2:
        return False, "sem ofertas suficientes no Google para comparar o preço"
    if check["min"] < landed:
        return False, (
            f"concorrente vende por R$ {check['min']:.2f}, abaixo do nosso custo com frete (R$ {landed:.2f})"
        )
    if price > check["median"] * 1.25:
        return False, f"preço R$ {price:.2f} muito acima da mediana do mercado (R$ {check['median']:.2f})"
    return True, f"{check['count']} ofertas; mínimo R$ {check['min']:.2f}, mediana R$ {check['median']:.2f}"
