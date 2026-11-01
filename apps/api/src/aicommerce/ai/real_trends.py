"""Mercado Livre public trend research (read-only, evidence preserving).

Research data is NOT channel eligibility, supplier proof, or permission to sell.
No cookies, browser automation, scraping, purchases, or account mutations.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx


def _why(exc: Exception) -> str:
    """Motivo curto e seguro: tipo do erro e, se houver, o código HTTP (401/403/429/5xx) — nunca corpo/cabeçalhos."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class ResearchUnavailable(RuntimeError):
    """No verified trend dataset available; caller must not invent results."""


class MercadoLivreTrendSource:
    name = "mercadolivre-trends-readonly"
    ENDPOINT = "https://api.mercadolibre.com/trends/MLB"

    def __init__(self, *, access_token: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._access_token = access_token
        self._transport = transport

    def search(self, topic: str, limit: int = 5) -> list[dict[str, Any]]:
        query = topic.strip()
        if not self._access_token:
            raise ResearchUnavailable("Mercado Livre Trends exige access token autorizado")
        if not query:
            raise ResearchUnavailable("Pesquisa sem tema")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(8.0),
                follow_redirects=False,
                transport=self._transport,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                    "User-Agent": "AICommerceResearch/1.0",
                },
            ) as client:
                response = client.get(self.ENDPOINT)
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchUnavailable(f"Fonte Mercado Livre indisponível ({_why(exc)})") from exc
        if not isinstance(payload, list):
            raise ResearchUnavailable("Formato da fonte não verificado")
        words = set(query.casefold().split())
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload[:1000]:
            if not isinstance(item, dict):
                continue
            raw = item.get("keyword")
            if not isinstance(raw, str):
                continue
            keyword = raw.strip()
            if not keyword or len(keyword) > 200 or keyword.casefold() in seen:
                continue
            if words and not (words & set(keyword.casefold().split())):
                continue
            seen.add(keyword.casefold())
            source_url = item.get("url", self.ENDPOINT)
            if not isinstance(source_url, str) or not source_url.startswith("https://lista.mercadolivre.com.br/"):
                source_url = self.ENDPOINT
            results.append(
                {
                    "title": keyword,
                    "category": "Mercado Livre Brasil",
                    "metric": "Presente na lista de tendências; volume não divulgado",
                    "date": dt.datetime.now(dt.UTC).date().isoformat(),
                    "url": source_url,
                    "source_api": self.ENDPOINT,
                    "simulated": False,
                    "verified_sales": False,
                }
            )
            if len(results) >= max(1, min(limit, 20)):
                break
        return results
