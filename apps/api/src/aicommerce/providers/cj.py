"""CJdropshipping API v2 — somente leitura (catálogo, estoque por armazém, frete e rastreio).

Criar pedido/pagar NÃO existe aqui: isso gasta dinheiro e terá de passar pelo executor (aprovação + caixa, ADR 0008).
A chave (AIC_CJ_API_KEY) troca-se por um access token de 180 dias, guardado só em arquivo privado 0600 fora do Git;
nunca vai para log, URL ou resposta. A CJ limita a 1 chamada por segundo: o cliente espaça as chamadas.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

BASE = "https://developers.cjdropshipping.com/api2.0/v1"
GET_PATHS = {
    "/product/listV2",
    "/product/query",
    "/product/variant/query",
    "/logistic/trackInfo",
    "/product/globalWarehouseList",
}
POST_PATHS = {"/authentication/getAccessToken", "/authentication/refreshAccessToken", "/logistic/freightCalculate"}
Transport = Callable[[str, str, dict[str, str], dict[str, Any] | None], tuple[int, Any]]


class CJError(RuntimeError):
    """Falha da CJ sem expor chave, token ou corpo bruto."""


def _transport(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None) -> tuple[int, Any]:
    import httpx

    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        r = client.request(method, url, headers=headers, json=body)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


class CJClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        token_file: Path | None = None,
        send: Transport = _transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not api_key:
            raise CJError("CJ não configurada: falta AIC_CJ_API_KEY")
        self._key, self._file, self._send, self._sleep, self._clock = api_key, token_file, send, sleep, clock
        self._token: dict[str, Any] | None = None
        self._last = 0.0

    # ───── token ─────
    def _load(self) -> dict[str, Any] | None:
        if self._token is None and self._file and self._file.exists():
            try:
                self._token = json.loads(self._file.read_text())
            except ValueError:
                self._token = None
        return self._token

    def _store(self, data: dict[str, Any]) -> None:
        self._token = data
        if self._file:
            self._file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self._file.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._file)

    def _access_token(self) -> str:
        tok = self._load()
        now = self._clock()
        if tok and tok.get("access_expires_at", 0) > now + 3600:
            return tok["access_token"]
        if tok and tok.get("refresh_token") and tok.get("refresh_expires_at", 0) > now + 60:
            data = self._call(
                "POST", "/authentication/refreshAccessToken", {"refreshToken": tok["refresh_token"]}, auth=False
            )
        else:
            data = self._call("POST", "/authentication/getAccessToken", {"apiKey": self._key}, auth=False)
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise CJError("CJ não devolveu access token (chave inválida ou expirada?)")
        self._store(
            {
                "access_token": data["accessToken"],
                "refresh_token": data.get("refreshToken"),
                "access_expires_at": now + 170 * 86400,  # CJ: 180 dias; margem de 10
                "refresh_expires_at": now + 170 * 86400,
            }
        )
        return data["accessToken"]

    # ───── chamada genérica (lista fechada) ─────
    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        query: dict[str, Any] | None = None,
    ) -> Any:
        allowed = GET_PATHS if method == "GET" else POST_PATHS
        if path not in allowed:
            raise CJError(f"endpoint fora da lista somente leitura: {method} {path}")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            headers["CJ-Access-Token"] = self._access_token()
        wait = 1.1 - (self._clock() - self._last)  # CJ: 1 chamada por segundo
        if wait > 0:
            self._sleep(wait)
        url = BASE + path + ("?" + urlencode(query, doseq=True) if query else "")
        try:
            status, payload = self._send(method, url, headers, body)
        except Exception as exc:  # nunca propaga mensagem que possa conter cabeçalho/URL
            raise CJError(f"CJ indisponível ({type(exc).__name__})") from None
        finally:
            self._last = self._clock()
        if status != 200 or not isinstance(payload, dict):
            raise CJError(f"CJ HTTP {status}")
        ok = payload.get("result") is True or payload.get("success") is True or payload.get("code") in (0, 200)
        if not ok or payload.get("result") is False or payload.get("success") is False:
            raise CJError(f"CJ recusou: código {payload.get('code')}")  # sucesso vem como code 0 ou 200
        return payload.get("data")

    # ───── leitura ─────
    def products(self, keyword: str, *, country: str = "BR", page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        data = self._call(
            "GET",
            "/product/listV2",
            query={"keyWord": keyword[:100], "countryCode": country, "page": page, "size": max(1, min(size, 100))},
        )
        items: list[Any] = []
        if isinstance(data, dict):  # listV2 devolve content[].productList; versões antigas, list/productList
            for block in data.get("content") or []:
                items += block.get("productList", []) if isinstance(block, dict) else []
            items += data.get("productList") or data.get("list") or []
        return [p for p in items if isinstance(p, dict)]

    def product(self, pid: str, *, country: str = "BR") -> dict[str, Any] | None:
        data = self._call("GET", "/product/query", query={"pid": pid, "countryCode": country})
        return data if isinstance(data, dict) else None

    def variants(self, pid: str) -> list[dict[str, Any]]:
        data = self._call("GET", "/product/variant/query", query={"pid": pid})
        return [v for v in data or [] if isinstance(v, dict)]

    def freight(
        self, vid: str, *, quantity: int = 1, start: str = "BR", end: str = "BR", zip_code: str | None = None
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "startCountryCode": start,
            "endCountryCode": end,
            "products": [{"vid": vid, "quantity": quantity}],
        }
        if zip_code:
            body["zip"] = zip_code
        data = self._call("POST", "/logistic/freightCalculate", body)
        return [x for x in data or [] if isinstance(x, dict)]

    def track(self, numbers: list[str]) -> list[dict[str, Any]]:
        data = self._call("GET", "/logistic/trackInfo", query={"trackNumber": numbers[:10]})
        return [x for x in data or [] if isinstance(x, dict)]
