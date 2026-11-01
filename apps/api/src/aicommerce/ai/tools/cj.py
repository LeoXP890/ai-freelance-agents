"""Ferramentas da CJdropshipping (catálogo internacional, só leitura) e câmbio oficial (PTAX do Banco Central).

CJ não tem armazém no Brasil: o produto vem do exterior (prazo maior) e a venda exige a homologação de venda
internacional (TAX-02) — a ferramenta só PESQUISA. O custo total em reais é ESTIMADO: preço + frete em dólar pela
PTAX de venda + impostos de importação do Remessa Conforme (até US$ 50: 20% de II + ICMS ~20% "por dentro";
acima: 60% − US$ 20 + ICMS), sempre marcado como estimativa.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx

from aicommerce.ai.tools import Tool, ToolError, register
from aicommerce.config import get_settings

_client: Any = None
PTAX = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo("
    "dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
)


def _cj() -> Any:
    global _client
    if _client is None:
        from aicommerce.providers.cj import CJClient, CJError

        s = get_settings()
        key = s.cj_api_key.get_secret_value() if s.cj_api_key else None
        try:
            _client = CJClient(key, token_file=Path(s.cj_token_file) if s.cj_token_file else None)
        except CJError as exc:
            raise ToolError(str(exc)) from None
    return _client


def _wrap(fn):
    from aicommerce.providers.cj import CJError

    try:
        return fn()
    except CJError as exc:
        raise ToolError(f"CJ: {exc}") from None


def usd_brl(args: dict[str, Any]) -> dict[str, Any]:
    end = dt.date.today()
    start = end - dt.timedelta(days=7)
    try:
        r = httpx.get(
            PTAX,
            params={"@dataInicial": f"'{start:%m-%d-%Y}'", "@dataFinalCotacao": f"'{end:%m-%d-%Y}'", "$format": "json"},
            timeout=15.0,
        )
        rows = r.json().get("value") or []
    except (httpx.HTTPError, ValueError):
        raise ToolError("PTAX do Banco Central indisponível") from None
    if not rows:
        raise ToolError("PTAX sem cotação nos últimos 7 dias")
    last = rows[-1]
    return {
        "usd_brl": float(last["cotacaoVenda"]),
        "date": str(last.get("dataHoraCotacao", ""))[:10],
        "source": "PTAX venda — Banco Central do Brasil",
    }


def _first(d: dict[str, Any], *keys: str) -> Any:
    return next((d[k] for k in keys if d.get(k) not in (None, "")), None)


def search(args: dict[str, Any]) -> list[dict[str, Any]]:
    term = str(args.get("term") or "").strip()[:100]
    if len(term) < 2:
        raise ToolError("termo de busca curto demais")
    items = _wrap(lambda: _cj().products(term, country=str(args.get("country", "CN")), size=int(args.get("limit", 20))))
    out = []
    for p in items:
        price = _first(p, "sellPrice", "nowPrice", "productSellPrice")
        try:
            usd = float(str(price).split("-")[0]) if price is not None else None
        except ValueError:
            usd = None
        out.append(
            {
                "pid": _first(p, "id", "pid"),
                "title": str(_first(p, "nameEn", "productNameEn", "productName") or "")[:120],
                "price_usd": usd,
                "image": _first(p, "bigImage", "productImage"),
                "category": _first(p, "threeCategoryName", "categoryName"),
            }
        )
    return [o for o in out if o["pid"] and o["price_usd"]]


def freight(args: dict[str, Any]) -> list[dict[str, Any]]:
    pid = str(args.get("pid") or "")
    vid = str(args.get("vid") or "")
    if not vid and pid:
        variants = _wrap(lambda: _cj().variants(pid))
        vid = str(variants[0].get("vid") or "") if variants else ""
    if not vid:
        raise ToolError("variante do produto não encontrada (informe pid ou vid)")
    opts = _wrap(lambda: _cj().freight(vid, start=str(args.get("from", "CN")), end="BR", zip_code=args.get("zip")))
    out = [
        {"name": o.get("logisticName"), "price_usd": float(o.get("logisticPrice") or 0), "days": o.get("logisticAging")}
        for o in opts
        if o.get("logisticPrice") is not None
    ]
    return sorted(out, key=lambda o: o["price_usd"])[:5]


def import_landed_brl(price_usd: float, freight_usd: float, usd_brl: float) -> dict[str, float]:
    """Remessa Conforme (estimativa): até US$ 50 II 20%; acima II 60% − US$ 20; ICMS 20% por dentro."""
    base = price_usd + freight_usd
    ii = base * 0.20 if base <= 50 else max(0.0, base * 0.60 - 20)
    icms = (base + ii) / (1 - 0.20) * 0.20
    total = (base + ii + icms) * usd_brl
    return {
        "goods_freight_brl": round(base * usd_brl, 2),
        "import_tax_brl": round((ii + icms) * usd_brl, 2),
        "landed_brl": round(total, 2),
    }


register(
    Tool(
        "fx.usd_brl",
        "Dólar oficial (PTAX)",
        "Cotação de venda do dólar pela PTAX do Banco Central.",
        {},
        usd_brl,
        cache_s=6 * 3600,
        daily_limit=50,
        source="Banco Central do Brasil",
    )
)
register(
    Tool(
        "cj.search",
        "Buscar no catálogo da CJ",
        "Produtos internacionais da CJdropshipping: nome, preço em dólar e foto.",
        {
            "term": "termo em inglês ou português",
            "limit": "itens (padrão 20)",
            "country": "armazém de origem (padrão CN)",
        },
        search,
        cache_s=12 * 3600,
        daily_limit=200,
        required=("term",),
        source="CJdropshipping API (leitura)",
    )
)
register(
    Tool(
        "cj.freight",
        "Frete da CJ até o Brasil",
        "Opções de frete internacional até o Brasil: preço em dólar e prazo.",
        {"pid": "produto da CJ", "vid": "variante (opcional)", "zip": "CEP (opcional)"},
        freight,
        cache_s=24 * 3600,
        daily_limit=200,
        source="CJdropshipping API (leitura)",
    )
)
