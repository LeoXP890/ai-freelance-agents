"""WooCommerce (COM-004, gate CH-WOO).

* `to_woo_product`: mapeamento canônico → WooCommerce REST API v3 (`POST /wp-json/wc/v3/products`).
* `verify_webhook_signature`: X-WC-Webhook-Signature = base64(HMAC-SHA256(segredo, corpo bruto)).
* `MockWooCommerceProvider`: DRY_RUN; valida o payload contra o subconjunto do schema oficial.
* `WooCommerceRestProvider`: cliente real (publicar, atualizar, pausar, reconciliar). Só envia em LIVE com credenciais
  da loja E gate CH-WOO aprovado; testado contra WooCommerce real no sandbox local (scripts/woo_live_check.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import pathlib
from decimal import Decimal
from functools import lru_cache
from typing import Any, ClassVar

import jsonschema

from aicommerce.providers.contracts import Capability, CommandEnvelope, ErrorKind, ProviderResult, ResultStatus
from aicommerce.providers.mock import MockCommerceProvider

SCHEMA_PATH = "providers/woocommerce-product.v3.subset.schema.json"


@lru_cache
def product_schema() -> dict:
    here = pathlib.Path(__file__).resolve()
    for base in (*here.parents,):
        candidate = base / "packages" / "contracts" / SCHEMA_PATH
        if candidate.exists():
            return json.loads(candidate.read_text())
    bundled = here.parent / "woocommerce-product.v3.subset.schema.json"  # cópia empacotada na imagem
    return json.loads(bundled.read_text())


def money_str(minor: int) -> str:
    return f"{Decimal(minor) / 100:.2f}"


def to_woo_product(
    *,
    listing_id: str,
    title: str,
    description: str,
    sku: str,
    price_minor: int,
    stock_qty: int,
    weight_g: int,
    dims_mm: tuple[int, int, int],
    images: list[str],
    category: str,
    simulated: bool,
    status: str = "publish",
) -> dict[str, Any]:
    length, width, height = (f"{Decimal(v) / 10:.1f}" for v in dims_mm)  # Woo usa cm por padrão
    return {
        "name": title,
        "type": "simple",
        "status": status,
        "regular_price": money_str(price_minor),
        "sku": sku,
        "description": description,
        "short_description": description[:160],
        "manage_stock": True,
        "stock_quantity": max(0, stock_qty),
        "weight": f"{Decimal(weight_g) / 1000:.3f}",
        "dimensions": {"length": length, "width": width, "height": height},
        "images": [{"src": src} for src in images],
        "meta_data": [
            {"key": "aic_listing_id", "value": listing_id},
            {"key": "aic_category", "value": category},
            {"key": "aic_simulated", "value": "true" if simulated else "false"},
        ],
    }


def validate_product(payload: dict[str, Any]) -> list[str]:
    v = jsonschema.Draft202012Validator(product_schema())
    return [f"{'/'.join(map(str, e.path)) or '(raiz)'}: {e.message}" for e in v.iter_errors(payload)]


def sign(secret: str, raw_body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()


def verify_webhook_signature(secret: str, raw_body: bytes, signature: str | None) -> bool:
    return bool(signature) and hmac.compare_digest(sign(secret, raw_body), signature or "")


class MockWooCommerceProvider(MockCommerceProvider):
    def __init__(self) -> None:
        super().__init__("WOOCOMMERCE")

    def execute(self, cmd: CommandEnvelope) -> ProviderResult:
        if cmd.operation in ("publish_listing", "update_listing"):
            body = {k: v for k, v in cmd.payload.items() if not k.startswith("_")}
            errors = validate_product(body)
            if errors:
                return ProviderResult(
                    ResultStatus.FAILED,
                    error_kind=ErrorKind.VALIDATION,
                    detail={"reason": "payload fora do contrato WooCommerce v3: " + "; ".join(errors[:5])},
                )
        return super().execute(cmd)


def _http(
    method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None, timeout: float, *, form: bool = False
) -> tuple[int, Any]:
    """HTTP mínimo (stdlib). Timeout/queda de conexão sobem como exceção — o chamador decide se é UNKNOWN.
    `form=True` envia o corpo como application/x-www-form-urlencoded (endpoints OAuth)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    if body is None:
        data = None
    elif form:
        data = urllib.parse.urlencode(body).encode()
    else:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        # The transport is shared with ML: never forward Basic/Bearer credentials to a redirect target.
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {}


class WooCommerceRestProvider:
    """Cliente REAL da loja WooCommerce (REST API v3). Só envia em LIVE, com credenciais configuradas E o gate
    CH-WOO aprovado pelo proprietário; fora disso a capacidade não é SUPPORTED e o executor falha tipado (nunca finge).

    Idempotência: o SKU é único na loja. Antes de criar, procura pelo SKU — se o produto já existe e pertence ao mesmo
    anúncio (meta aic_listing_id), a publicação é reconhecida (replay) em vez de duplicar. A reconciliação após timeout
    consulta a loja pelo SKU/ID."""

    name = "woocommerce-rest"
    ROUTES: ClassVar[dict[str, tuple[str, str]]] = {
        "publish_listing": ("POST", "/wp-json/wc/v3/products"),
        "update_listing": ("PUT", "/wp-json/wc/v3/products/{external_id}"),
        "pause_listing": ("PUT", "/wp-json/wc/v3/products/{external_id}"),
    }
    GATE = "CH-WOO"

    def __init__(
        self,
        base_url: str | None = None,
        consumer_key: str | None = None,
        consumer_secret: str | None = None,
        *,
        gate_approved: bool = False,
        http=None,
        timeout: float = 30.0,
    ) -> None:
        from aicommerce.config import get_settings

        s = get_settings()
        self.base_url = (base_url if base_url is not None else s.woo_base_url).rstrip("/")
        self._key = (
            consumer_key
            if consumer_key is not None
            else (s.woo_consumer_key.get_secret_value() if s.woo_consumer_key else "")
        )
        self._secret = (
            consumer_secret
            if consumer_secret is not None
            else (s.woo_consumer_secret.get_secret_value() if s.woo_consumer_secret else "")
        )
        self.gate_approved = gate_approved
        self._http = http or _http
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self._key and self._secret)

    def capability(self, operation: str, mode: str) -> Capability:
        if operation not in self.ROUTES or mode == "DRY_RUN" or not self.configured:
            return Capability.UNAVAILABLE
        if not self.gate_approved:
            return Capability.BLOCKED_BY_POLICY
        return Capability.SUPPORTED

    def build_request(self, cmd: CommandEnvelope, external_id: str | None = None) -> dict[str, Any]:
        method, path = self.ROUTES[cmd.operation]
        body = {k: v for k, v in cmd.payload.items() if not k.startswith("_")}
        if cmd.operation == "pause_listing":
            body = {"status": "draft"}
        return {
            "method": method,
            "url": self.base_url + path.format(external_id=external_id or "{id}"),
            "json": body,
            "headers": {"Idempotency-Key": cmd.idempotency_key, "X-Request-ID": cmd.request_id},
            "auth": "basic(consumer_key, consumer_secret) via secret manager",
        }

    # ── HTTP ──
    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, request_id: str = ""
    ) -> tuple[int, Any]:
        token = base64.b64encode(f"{self._key}:{self._secret}".encode()).decode()
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Basic {token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        return self._http(method, self.base_url + path, headers, body, self._timeout)

    @staticmethod
    def _meta(product: dict[str, Any], key: str) -> str | None:
        return next((str(m.get("value")) for m in product.get("meta_data") or [] if m.get("key") == key), None)

    def _find_by_sku(self, sku: str) -> dict[str, Any] | None:
        from urllib.parse import quote

        status, data = self._call("GET", f"/wp-json/wc/v3/products?sku={quote(sku)}&status=any")
        if status != 200:
            raise _WooHTTPError(status, data)
        return data[0] if isinstance(data, list) and data else None

    @staticmethod
    def _failed(status: int, data: Any, op: str) -> ProviderResult:
        msg = (data or {}).get("message", "") if isinstance(data, dict) else ""
        code = (data or {}).get("code", "") if isinstance(data, dict) else ""
        if status in (408, 429) or status >= 500:
            kind = ErrorKind.TRANSIENT
        elif status in (401, 403):
            kind = ErrorKind.REJECTED  # credencial recusada/sem permissão de escrita: refazer não resolve
        elif status == 400:
            kind = ErrorKind.VALIDATION
        else:
            kind = ErrorKind.REJECTED
        return ProviderResult(
            ResultStatus.FAILED, error_kind=kind, detail={"http": status, "code": code, "reason": msg[:300], "op": op}
        )

    def execute(self, cmd: CommandEnvelope) -> ProviderResult:
        cap = self.capability(cmd.operation, cmd.mode)
        if cap is not Capability.SUPPORTED:
            return ProviderResult(
                ResultStatus.FAILED,
                error_kind=ErrorKind.UNSUPPORTED if cap is Capability.UNAVAILABLE else ErrorKind.POLICY_DENIED,
                detail={
                    "reason": "loja WooCommerce real não configurada"
                    if cap is Capability.UNAVAILABLE
                    else "gate CH-WOO não aprovado"
                },
            )
        body = {k: v for k, v in cmd.payload.items() if not k.startswith("_")}
        sent = False
        try:
            if cmd.operation == "pause_listing":
                ext = str(cmd.payload.get("external_id") or "")
                if not ext:
                    return ProviderResult(
                        ResultStatus.FAILED, error_kind=ErrorKind.VALIDATION, detail={"reason": "pausa sem external_id"}
                    )
                sent = True
                status, data = self._call("PUT", f"/wp-json/wc/v3/products/{ext}", {"status": "draft"}, cmd.request_id)
                if status == 200:
                    return ProviderResult(
                        ResultStatus.SUCCEEDED,
                        provider_ref=str(data.get("id", ext)),
                        detail={"status": data.get("status")},
                    )
                return self._failed(status, data, cmd.operation)
            errors = validate_product(body)
            if errors:
                return ProviderResult(
                    ResultStatus.FAILED,
                    error_kind=ErrorKind.VALIDATION,
                    detail={"reason": "payload fora do contrato WooCommerce v3: " + "; ".join(errors[:5])},
                )
            sku = str(body.get("sku") or "")
            listing_id = self._meta(body, "aic_listing_id")
            existing = self._find_by_sku(sku) if sku else None
            if cmd.operation == "publish_listing":
                if existing is not None:
                    if self._meta(existing, "aic_listing_id") == listing_id:  # já criado (retry/replay): não duplica
                        return ProviderResult(
                            ResultStatus.SUCCEEDED, provider_ref=str(existing["id"]), detail={"replayed": True}
                        )
                    return ProviderResult(
                        ResultStatus.FAILED,
                        error_kind=ErrorKind.REJECTED,
                        detail={"reason": f"SKU {sku} já existe na loja para outro produto"},
                    )
                body["meta_data"] = [
                    *(body.get("meta_data") or []),
                    {"key": "aic_idempotency_key", "value": cmd.idempotency_key},
                ]
                sent = True
                status, data = self._call("POST", "/wp-json/wc/v3/products", body, cmd.request_id)
                if status in (200, 201):
                    return ProviderResult(
                        ResultStatus.SUCCEEDED, provider_ref=str(data["id"]), detail={"status": data.get("status")}
                    )
                return self._failed(status, data, cmd.operation)
            if cmd.operation == "update_listing":
                if existing is None:
                    return ProviderResult(
                        ResultStatus.FAILED,
                        error_kind=ErrorKind.REJECTED,
                        detail={"reason": f"produto SKU {sku} não encontrado na loja (removido manualmente?)"},
                    )
                sent = True
                status, data = self._call("PUT", f"/wp-json/wc/v3/products/{existing['id']}", body, cmd.request_id)
                if status == 200:
                    return ProviderResult(
                        ResultStatus.SUCCEEDED, provider_ref=str(data["id"]), detail={"status": data.get("status")}
                    )
                return self._failed(status, data, cmd.operation)
        except _WooHTTPError as e:
            return self._failed(e.status, e.data, cmd.operation)
        except (TimeoutError, OSError) as e:
            # depois do envio não dá para saber se a loja aplicou: UNKNOWN → reconciliação consulta antes de repetir
            if sent:
                return ProviderResult(
                    ResultStatus.UNKNOWN, error_kind=ErrorKind.TIMEOUT, detail={"exception": type(e).__name__}
                )
            return ProviderResult(
                ResultStatus.FAILED, error_kind=ErrorKind.TRANSIENT, detail={"exception": type(e).__name__}
            )
        return ProviderResult(
            ResultStatus.FAILED, error_kind=ErrorKind.UNSUPPORTED, detail={"reason": f"operação {cmd.operation}"}
        )

    def lookup(self, idempotency_key: str, payload: dict[str, Any] | None = None) -> ProviderResult | None:
        """Reconciliação: o efeito existe na loja? (SKU do payload; pausa = produto em rascunho)."""
        if not self.configured or not payload:
            return None
        try:
            if payload.get("external_id") and set(payload) <= {"channel", "external_id", "reason"}:  # pausa
                status, data = self._call("GET", f"/wp-json/wc/v3/products/{payload['external_id']}")
                if status == 200 and data.get("status") == "draft":
                    return ProviderResult(
                        ResultStatus.SUCCEEDED, provider_ref=str(data["id"]), detail={"reconciled": True}
                    )
                return None
            found = self._find_by_sku(str(payload.get("sku") or ""))
        except (_WooHTTPError, TimeoutError, OSError):
            return None
        if found and (
            self._meta(found, "aic_idempotency_key") == idempotency_key
            or self._meta(found, "aic_listing_id") == self._meta(payload, "aic_listing_id")
        ):
            return ProviderResult(ResultStatus.SUCCEEDED, provider_ref=str(found["id"]), detail={"reconciled": True})
        return None


class _WooHTTPError(Exception):
    def __init__(self, status: int, data: Any) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.data = status, data
