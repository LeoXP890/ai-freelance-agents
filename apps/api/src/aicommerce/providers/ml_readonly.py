"""Explicitly authorized read-only ML diagnostics. No commerce methods or automatic token refresh.

Separate from the application's DRY_RUN credentials: an owner may supply a consented access token
to the CLI without enabling LIVE. Replies are kept in memory; receipts omit personal information.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.mercadolibre.com"
PATHS = (
    r"/users/me",
    r"/users/\d+",
    r"/sites/MLB/categories",
    r"/categories/MLB\d+(?:/attributes)?",
    r"/sites/MLB/listing_prices",
    r"/items/MLB\d+",
    r"/orders/\d+",
    r"/shipments/\d+",
    # Pesquisa de mercado (ai/ml_market_research.py): mais vendidos, tendências e produto de catálogo.
    r"/highlights/MLB/category/MLB\d+",
    r"/trends/MLB(?:/MLB\d+)?",
    r"/products/MLB\d+(?:/items)?",
    r"/categories/MLB\d+/shipping_preferences",
    r"/users/\d+/shipping_options/free",  # custo do frete grátis para o vendedor (cotação, sem efeito)
    # Ferramentas dos agentes (ai/tools/ml.py): mesmo produto pelo código de barras e categoria pelo título.
    r"/products/search",
    r"/sites/MLB/domain_discovery/search",
    r"/users/\d+/items/search",  # meus anúncios (conferir o que o robô publicou)
)
QUERY = {
    "/users/*/items/search": {"q", "status", "limit", "seller_sku"},
    "/products/search": {"site_id", "status", "product_identifier", "q", "limit"},
    "/sites/MLB/domain_discovery/search": {"q", "limit"},
    "/sites/MLB/listing_prices": {"price", "category_id", "listing_type_id"},
    "/products/MLB*/items": {"limit"},
    "/users/*/shipping_options/free": {
        "dimensions",
        "item_price",
        "listing_type_id",
        "mode",
        "condition",
        "logistic_type",
        "verbose",
    },
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def transport(path: str, token: str) -> tuple[int, Any, dict]:
    request = urllib.request.Request(
        API + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, method="GET"
    )
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=15)
    except urllib.error.HTTPError as exc:
        return exc.code, {}, dict(exc.headers)
    with response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response exceeds diagnostic limit")
        return response.status, json.loads(raw), dict(response.headers)


@dataclass
class ReadResult:
    status: str
    http: int | None
    attempts: int
    data: Any = None


class MLReadOnly:
    def __init__(self, token: str, *, send=transport, sleep=time.sleep, monotonic=time.monotonic):
        if not token or any(ch.isspace() for ch in token):
            raise ValueError("invalid token format")
        self._token, self._send, self._sleep, self._clock = token, send, sleep, monotonic
        self._next = 0.0

    def get(self, path: str) -> ReadResult:
        from urllib.parse import parse_qs, urlsplit

        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or not any(re.fullmatch(p, parsed.path) for p in PATHS):
            raise ValueError("endpoint outside read-only allowlist")
        query = parse_qs(parsed.query, keep_blank_values=True)
        key = re.sub(r"^/products/MLB\d+/items$", "/products/MLB*/items", parsed.path)
        key = re.sub(r"^/users/\d+/shipping_options/free$", "/users/*/shipping_options/free", key)
        key = re.sub(r"^/users/\d+/items/search$", "/users/*/items/search", key)
        if query and (key not in QUERY or set(query) - QUERY[key] or any(len(v) != 1 for v in query.values())):
            raise ValueError("unsupported query parameters")
        for attempt in range(1, 4):
            self._sleep(max(0, self._next - self._clock()))
            self._next = self._clock() + 1.0  # conservative local pacing; not a claimed platform quota
            try:
                status, data, headers = self._send(path, self._token)
            except (OSError, TimeoutError):
                status, data, headers = 503, None, {}
            except (ValueError, TypeError):
                return ReadResult("INVALID_RESPONSE", None, attempt)
            if status == 200:
                if not isinstance(data, (dict, list)):
                    return ReadResult("INVALID_RESPONSE", status, attempt)
                return ReadResult("OK", status, attempt, data)
            if status == 401:
                return ReadResult("REAUTH_REQUIRED", status, attempt)
            if status == 403:
                return ReadResult("PERMISSION_DENIED", status, attempt)
            if status not in (408, 429) and status < 500:
                return ReadResult("REJECTED", status, attempt)
            retry_after = str(next((v for k, v in headers.items() if k.lower() == "retry-after"), ""))
            # Never retry earlier than Retry-After; defer instead of sleeping unboundedly.
            if retry_after and (not retry_after.isdigit() or int(retry_after) > 30):
                return ReadResult("DEFERRED_RATE_LIMIT", status, attempt)
            if attempt < 3:
                self._sleep(max(2**attempt, int(retry_after or 0)))
        return ReadResult("RATE_LIMITED" if status == 429 else "UNAVAILABLE", status, attempt)

    def receipt(self) -> dict:
        result = self.get("/users/me")
        valid = result.status == "OK" and isinstance(result.data, dict) and str(result.data.get("id", "")).isdigit()
        return {
            "checked_at": dt.datetime.now(dt.UTC).isoformat(),
            "state": "READ_ONLY_VERIFICADO"
            if valid
            else result.status
            if result.status != "OK"
            else "INVALID_RESPONSE",
            "endpoint": "/users/me",
            "http": result.http,
            "attempts": result.attempts,
            "account_id": "***" + str(result.data["id"])[-3:] if valid else None,
            "site_id": result.data.get("site_id") if valid else None,
            "commerce_effects": 0,
            "oauth_refresh_performed": False,
            "CH_ML_approved": False,
            "eligibility_verified": False,
        }
