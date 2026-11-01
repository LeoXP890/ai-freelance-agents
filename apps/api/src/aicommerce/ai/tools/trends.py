"""Ferramentas de tendência fora do Mercado Livre: Google (feed diário público e busca pelo Gemini) e calendário.

O Google Trends "explorar" bloqueia acesso automático (HTTP 429) e não é contornado. O feed diário traz mais assunto
do que produto: só os títulos e o volume aproximado entram; os links do feed nunca são abertos (já vieram links
suspeitos nele).
"""

from __future__ import annotations

import datetime as dt
import html
import re
from typing import Any

import httpx

from aicommerce.ai.tools import Tool, ToolError, register

FEED = "https://trends.google.com/trending/rss?geo=BR"
_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_TRAFFIC = re.compile(r"<ht:approx_traffic>(.*?)</ht:approx_traffic>", re.S)
# assunto do dia que nunca é produto (esporte, política, celebridade, loteria…): fora, sem gastar token
_NOT_PRODUCT = re.compile(
    r"\b(x|vs|jogo|ao vivo|placar|resultado|elei|prefeit|governo|presidente|morre|morte|lotof|mega.?sena|quina|"
    r"bbb|novela|filme|série|show|futebol|campeonato|copa|libertadores|brasileir[aã]o|nba|nfl|ufc|f1)\b",
    re.I,
)


def google_daily(args: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = c.get(FEED)
    except httpx.HTTPError as exc:
        raise ToolError(f"feed do Google Trends indisponível ({type(exc).__name__})") from None
    if r.status_code != 200 or len(r.content) > 2_000_000:
        raise ToolError(f"feed do Google Trends respondeu HTTP {r.status_code}")
    out = []
    for block in _ITEM.findall(r.text)[:60]:
        t = _TITLE.search(block)
        title = html.unescape(t.group(1)).strip() if t else ""
        if not title or _NOT_PRODUCT.search(title):
            continue
        traffic = _TRAFFIC.search(block)
        out.append({"term": title[:80], "traffic": traffic.group(1).strip() if traffic else None})
    return out[: int(args.get("limit", 20))]


def google_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    from aicommerce.ai.specs import _web_sources
    from aicommerce.ai.web_research import GEMINI_TRENDS_PROMPT

    topic = str(args.get("topic") or "casa, cozinha, pet e utilidades").strip()[:80]
    q = f"{topic} — {dt.date.today().strftime('%m/%Y')}"
    sources, note = _web_sources(q, limit=int(args.get("limit", 10)), prompt=GEMINI_TRENDS_PROMPT)
    if not sources:
        raise ToolError(note or "busca do Google indisponível (pesquisa web desligada ou teto atingido)")
    return [
        {"product": s.get("title", "")[:80], "signal": (s.get("text") or "")[:160], "source": s.get("url")}
        for s in sources
    ]


# Datas do varejo brasileiro: (mês, dia, nome, dias de antecedência para preparar o estoque/anúncio)
CALENDAR = [
    (1, 15, "Volta às aulas", 45),
    (2, 14, "Carnaval (aprox.)", 30),
    (3, 15, "Dia do Consumidor", 21),
    (4, 5, "Páscoa (aprox.)", 30),
    (5, 10, "Dia das Mães (2º domingo)", 35),
    (6, 12, "Dia dos Namorados", 30),
    (8, 9, "Dia dos Pais (2º domingo)", 30),
    (9, 15, "Dia do Cliente", 21),
    (10, 12, "Dia das Crianças", 35),
    (11, 27, "Black Friday (última sexta)", 45),
    (12, 25, "Natal", 45),
]


def seasonal(args: dict[str, Any]) -> list[dict[str, Any]]:
    today = dt.date.fromisoformat(args["today"]) if args.get("today") else dt.date.today()
    out = []
    for m, d, name, prep in CALENDAR:
        date = dt.date(today.year, m, d)
        if date < today:
            date = dt.date(today.year + 1, m, d)
        days = (date - today).days
        if days <= int(args.get("horizon_days", 60)):
            out.append(
                {
                    "event": name,
                    "date": date.isoformat(),
                    "days_left": days,
                    "act_now": days <= prep,
                    "prepare_days": prep,
                }
            )
    return sorted(out, key=lambda e: e["days_left"])


register(
    Tool(
        "trends.google_daily",
        "Em alta no Google Brasil (dia)",
        "Termos do feed diário do Google Trends BR, sem esporte/política/celebridade; links ignorados.",
        {"limit": "máximo de termos (padrão 20)"},
        google_daily,
        cache_s=3 * 3600,
        daily_limit=24,
        source="Google Trends (feed RSS público)",
    )
)
register(
    Tool(
        "trends.google_search",
        "Busca do Google sobre o que está em alta",
        "Gemini com Google Search: o que está em alta para comprar no tema, com fontes.",
        {"topic": "tema ou categoria", "limit": "produtos (padrão 10)"},
        google_search,
        cost_micro_usd=3_000,
        cache_s=12 * 3600,
        daily_limit=20,
        source="Google Search via Gemini (custo no teto de IA)",
    )
)
register(
    Tool(
        "trends.seasonal",
        "Calendário do varejo",
        "Próximas datas comerciais do Brasil e se já é hora de preparar.",
        {"horizon_days": "janela em dias (padrão 60)"},
        seasonal,
        cache_s=12 * 3600,
        source="calendário comercial brasileiro",
    )
)
