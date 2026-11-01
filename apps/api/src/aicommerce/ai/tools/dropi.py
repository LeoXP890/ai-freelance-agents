"""Ferramentas do Dropi (catálogo Dropnacional) com a sessão do proprietário — só leitura.

O Dropi não tem API pública para lojista: usamos as mesmas chamadas do painel com a sessão salva pelo proprietário
(scripts/dropi_login.py; arquivo 0600 em AIC_DROPI_SESSION_FILE, nunca a senha). Sessão vencida → erro claro pedindo
novo login. O contrato das chamadas pode mudar sem aviso: respostas fora do formato falham, nunca inventam dado.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from aicommerce.ai.tools import Tool, ToolError, register
from aicommerce.config import get_settings

BASE = "https://app.dropi.com.br"
SEARCH = "/MarketPlace/carregaMarketPlace/dropnacional"


def _cookies() -> dict[str, str]:
    path = get_settings().dropi_session_file
    if not path or not Path(path).exists():
        raise ToolError("Dropi não conectado neste servidor: o proprietário precisa entrar na janela de login do Dropi")
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ToolError("sessão do Dropi ilegível: refazer o login") from None
    return {c["name"]: c["value"] for c in data.get("cookies", []) if "dropi" in str(c.get("domain", ""))}


def _post(path: str, form: dict[str, str]) -> Any:
    try:
        with httpx.Client(
            base_url=BASE,
            timeout=25.0,
            follow_redirects=False,
            cookies=_cookies(),
            headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"},
        ) as c:
            r = c.post(path, data=form)
    except httpx.HTTPError as exc:
        raise ToolError(f"Dropi indisponível ({type(exc).__name__})") from None
    if r.status_code in (301, 302, 401, 403) or "login" in r.headers.get("location", ""):
        raise ToolError("sessão do Dropi expirou: o proprietário precisa entrar de novo na janela de login")
    if r.status_code != 200:
        raise ToolError(f"Dropi respondeu HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise ToolError("sessão do Dropi expirou ou resposta fora do formato: refazer o login") from None


def _item(p: dict[str, Any]) -> dict[str, Any]:
    forn = p.get("dados_fornecedor") or {}
    img = p.get("imagem")
    return {
        "title": str(p.get("title") or "")[:120],
        "cost": float(p.get("valor") or 0),
        "stock": int(float(p.get("estoque_geral") or 0)),
        "supplier": (forn.get("nome_loja") or forn.get("nome")) if isinstance(forn, dict) else None,
        "supplier_id": p.get("fornecedor"),
        "orders_total": p.get("total_pedidos"),
        "visits_30d": p.get("visitas_30"),
        "rating": p.get("media_avaliacoes"),
        "requires_invoice": str(p.get("obrigatorio_nf")) == "1",
        "dropi_id": p.get("id_produto_fornecedor") or p.get("product_id"),
        "image": img if isinstance(img, str) and img.startswith("https://") else None,
        "imported": bool(p.get("importado")),
        "import_url": p.get("link_importar") if str(p.get("link_importar") or "").startswith(BASE) else None,
    }


SORTS = {"maisvistos30", "maisvistos7", "maisavaliacoes", "melhoravaliados", "lancamentos", ""}


def search(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Por termo e/ou categoria do Dropi, com a ordenação do painel (ex.: mais vistos no mês)."""
    term = str(args.get("term") or "").strip()[:80]
    cats = [c for c in str(args.get("category_ids") or "").replace(" ", "").split(",") if c.isdigit()]
    sort = str(args.get("sort") or "")
    if sort not in SORTS:
        raise ToolError(f"ordenação inválida; use {sorted(x for x in SORTS if x)}")
    if len(term) < 2 and not cats:
        raise ToolError("informe um termo de busca ou uma categoria")
    out: list[dict[str, Any]] = []
    first = int(args.get("start_page", 0))
    for page in range(first, first + int(args.get("pages", 1))):
        form: dict[str, Any] = {"page": str(page), "search": term, "filtro": sort}
        if cats:
            form["categoria[]"] = cats
        data = _post(SEARCH, form)
        prods = ((data or {}).get("call") or {}).get("success", {}).get("produtos")
        if prods is None:
            raise ToolError("resposta do Dropi fora do formato esperado")
        out += [_item(p) for p in prods if isinstance(p, dict)]
        if not prods:
            break
    min_stock = int(args.get("min_stock", 3))
    return [p for p in out if p["stock"] >= min_stock][: int(args.get("limit", 30))]


register(
    Tool(
        "dropi.search",
        "Buscar no catálogo do Dropi",
        "Produtos do Dropnacional por termo: custo, estoque, fornecedor, pedidos e se exige nota.",
        {
            "term": "termo de busca (opcional se houver categoria)",
            "category_ids": "categorias do Dropi separadas por vírgula (ex.: 80,83)",
            "sort": "maisvistos30, maisvistos7, maisavaliacoes, melhoravaliados ou lancamentos",
            "start_page": "página inicial (0)",
            "min_stock": "estoque mínimo (padrão 3)",
            "limit": "máximo de itens (padrão 30)",
            "pages": "páginas (padrão 1)",
        },
        search,
        cache_s=6 * 3600,
        daily_limit=300,
        required=(),
        source="Dropi (sessão do proprietário)",
    )
)
