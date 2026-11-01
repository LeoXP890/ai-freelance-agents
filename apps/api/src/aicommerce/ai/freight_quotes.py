"""Sandbox-only freight quotes via Melhor Envio official API.

This adapter can CALCULATE a quote; it cannot create a cart, buy labels, print,
track or mutate shipments. Production endpoints are intentionally absent.
No personal name, street address or CPF is sent: only postal codes and volume.
Sandbox responses are NOT real commercial freight prices.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import httpx

SANDBOX_CALCULATE = "https://sandbox.melhorenvio.com.br/api/v2/me/shipment/calculate"
_CEP = re.compile(r"^\d{8}$")


def _why(exc: Exception) -> str:
    """Motivo curto e seguro: tipo do erro e, se houver, o código HTTP (401/403/429/5xx) — nunca corpo/cabeçalhos."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class FreightQuoteUnavailable(ValueError):
    pass


def _cep(value: str) -> str:
    cleaned = value.replace("-", "")
    if not _CEP.fullmatch(cleaned):
        raise FreightQuoteUnavailable("CEP deve conter oito dígitos")
    return cleaned


def _positive(value: Any, label: str, cap: int) -> float:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FreightQuoteUnavailable(f"{label}: número inválido") from exc
    if not d.is_finite() or d <= 0 or d > cap:
        raise FreightQuoteUnavailable(f"{label}: medida fora dos limites")
    return float(d)


def _money_minor(value: Any) -> int | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite() or d <= 0 or d > 100000:
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class MelhorEnvioSandboxQuote:
    name = "melhorenvio-sandbox-calculate"

    def __init__(
        self,
        token: str | None,
        user_agent: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.token, self.user_agent, self.transport = token, user_agent, transport

    def calculate(
        self,
        *,
        from_cep: str,
        to_cep: str,
        width_cm: Any,
        length_cm: Any,
        height_cm: Any,
        weight_kg: Any,
    ) -> list[dict[str, Any]]:
        if not self.token or not self.user_agent or "@" not in self.user_agent:
            raise FreightQuoteUnavailable("Token sandbox e User-Agent de suporte são obrigatórios")
        payload = {
            "from": {"postal_code": _cep(from_cep)},
            "to": {"postal_code": _cep(to_cep)},
            "volumes": [
                {
                    "width": _positive(width_cm, "largura", 200),
                    "length": _positive(length_cm, "comprimento", 200),
                    "height": _positive(height_cm, "altura", 200),
                    "weight": _positive(weight_kg, "peso", 100),
                }
            ],
        }
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(10.0),
                follow_redirects=False,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": self.user_agent,
                    "Authorization": f"Bearer {self.token}",
                },
            ) as client:
                response = client.post(SANDBOX_CALCULATE, json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FreightQuoteUnavailable(f"Cotação sandbox indisponível: {_why(exc)}") from exc
        if not isinstance(data, list):
            raise FreightQuoteUnavailable("Formato da cotação inválido")
        rows = []
        for service in data:
            if not isinstance(service, dict) or service.get("error") or service.get("error_code"):
                continue
            company = service.get("company")
            company_name = company.get("name", "") if isinstance(company, dict) else ""
            price = _money_minor(service.get("custom_price", service.get("price")))
            days = service.get("custom_delivery_time", service.get("delivery_time"))
            if price is None or not isinstance(days, int) or isinstance(days, bool) or not 0 < days < 365:
                continue
            if not isinstance(service.get("id"), int) or not isinstance(company_name, str):
                continue
            rows.append(
                {
                    "service_id": service["id"],
                    "service_name": str(service.get("name") or "")[:100],
                    "company_name": company_name[:100],
                    "price_minor": price,
                    "delivery_days": days,
                    "source_api": SANDBOX_CALCULATE,
                    "collected_on": dt.datetime.now(dt.UTC).date().isoformat(),
                    "sandbox": True,
                    "usable_for_commercial_sale": False,
                }
            )
        return sorted(rows, key=lambda x: (x["price_minor"], x["delivery_days"]))
