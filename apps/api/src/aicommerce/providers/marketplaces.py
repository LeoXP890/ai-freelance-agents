"""Contratos de marketplaces (COM-005 Mercado Livre, COM-006 Shopee, COM-007 TikTok Shop).

Grau de verificação (consulta pública em 2026-10-24; o portal de desenvolvedores do ML bloqueia leitura automatizada):
* Mercado Livre — campos do item (title, category_id, price, currency_id, available_quantity, buying_mode,
  listing_type_id, condition, pictures, attributes) e notificação (resource, user_id, topic, application_id,
  attempts, sent, received; modelo "notifica e busca o recurso") conferidos em exemplos da documentação oficial.
  OAuth (PKCE, expiração 6 h, refresh de uso único) e campos do pedido: de conhecimento prévio, NÃO conferidos.
* Shopee — assinatura v2 conferida apenas em biblioteca não oficial; campos de add_item NÃO conferidos.
* TikTok Shop — campos do create product e assinatura NÃO conferidos (documentação oficial não acessível).
Tudo marcado como `contract_status`; a homologação (gates CH-ML / CH-SHO / CH-TTS) é que valida de fato.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from decimal import Decimal
from functools import lru_cache
from typing import Any, ClassVar
from urllib.parse import urlencode

import jsonschema

from aicommerce.providers.contracts import Capability, CommandEnvelope, ErrorKind, ProviderResult, ResultStatus
from aicommerce.providers.mock import MockCommerceProvider

CONTRACT_STATUS = {
    "MERCADO_LIVRE": "VERIFIED_SUBSET",
    "SHOPEE": "UNVERIFIED",
    "TIKTOK_SHOP": "UNVERIFIED",
}


def _money(minor: int) -> Decimal:
    return (Decimal(minor) / 100).quantize(Decimal("0.01"))


# ───────────────────────────── Mercado Livre ─────────────────────────────
ML_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "category_id",
        "price",
        "currency_id",
        "available_quantity",
        "buying_mode",
        "listing_type_id",
        "condition",
        "pictures",
        "attributes",
        "seller_custom_field",
    ],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 60},
        "category_id": {"type": "string", "pattern": "^(MLB\\d+|SIM-MLB\\d+)$"},
        "price": {"type": "number", "exclusiveMinimum": 0},
        "currency_id": {"const": "BRL"},
        "available_quantity": {"type": "integer", "minimum": 0},
        "buying_mode": {"const": "buy_it_now"},
        "listing_type_id": {"enum": ["gold_special", "gold_pro"]},
        "condition": {"enum": ["new", "used"]},
        "pictures": {
            "type": "array",
            "items": {"type": "object", "required": ["source"], "properties": {"source": {"type": "string"}}},
        },
        "attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "value_name"],
                "properties": {"id": {"type": "string"}, "value_name": {"type": "string"}},
            },
        },
        "seller_custom_field": {"type": "string"},
    },
}


def to_ml_item(
    *,
    title: str,
    category_id: str,
    price_minor: int,
    stock_qty: int,
    images: list[str],
    sku: str,
    brand: str | None,
    gtin: str | None,
    listing_type: str = "gold_special",
) -> dict[str, Any]:
    attrs = [{"id": "SELLER_SKU", "value_name": sku}]
    if brand:
        attrs.append({"id": "BRAND", "value_name": brand})
    if gtin:
        attrs.append({"id": "GTIN", "value_name": gtin})
    return {
        "title": title[:60],
        "category_id": category_id,
        "price": float(_money(price_minor)),
        "currency_id": "BRL",
        "available_quantity": max(0, stock_qty),
        "buying_mode": "buy_it_now",
        "listing_type_id": listing_type,
        "condition": "new",
        "pictures": [{"source": u} for u in images],
        "attributes": attrs,
        "seller_custom_field": sku,
    }


ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API = "https://api.mercadolibre.com"
ML_TOPICS = {"orders_v2", "items", "questions", "payments", "shipments"}


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def ml_authorize_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    return (
        ML_AUTH_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def ml_token_request(grant: str, **kw: str) -> dict[str, Any]:
    """Monta (sem enviar) a requisição ao endpoint de token: authorization_code ou refresh_token."""
    if grant not in ("authorization_code", "refresh_token"):
        raise ValueError("grant inválido")
    return {
        "method": "POST",
        "url": ML_TOKEN_URL,
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "data": {"grant_type": grant, **kw},
    }


class MockMLOAuth:
    """Servidor OAuth simulado: access token de 6 h; refresh token de USO ÚNICO (rotaciona a cada uso).

    O estado (refresh tokens válidos) fica no banco, como num servidor externo real: API, worker e seed são
    processos diferentes e precisam enxergar a mesma verdade."""

    TTL = dt.timedelta(hours=6)

    @staticmethod
    def _key(token: str) -> str:
        return "refresh:" + hashlib.sha256(token.encode()).hexdigest()  # guarda só o hash do token

    def issue(self, now: dt.datetime | None = None) -> dict[str, Any]:
        from aicommerce.db import session_scope
        from aicommerce.models import SimChannelResource

        now = now or dt.datetime.now(dt.UTC)
        refresh = "SIM-RT-" + secrets.token_hex(8)
        with session_scope() as db:
            db.add(
                SimChannelResource(channel="OAUTH", resource=self._key(refresh), payload={"issued_at": now.isoformat()})
            )
        return {
            "access_token": "SIM-AT-" + secrets.token_hex(8),
            "refresh_token": refresh,
            "expires_at": now + self.TTL,
            "user_id": "SIM-" + secrets.token_hex(3),
        }

    def refresh(self, refresh_token: str, now: dt.datetime | None = None) -> dict[str, Any]:
        from sqlalchemy import delete

        from aicommerce.db import session_scope
        from aicommerce.models import SimChannelResource

        with session_scope() as db:
            gone = db.execute(
                delete(SimChannelResource)
                .where(SimChannelResource.channel == "OAUTH", SimChannelResource.resource == self._key(refresh_token))
                .returning(SimChannelResource.id)
            ).first()
        if gone is None:
            raise PermissionError("invalid_grant: refresh token já usado ou inválido")
        return self.issue(now)


MOCK_ML_OAUTH = MockMLOAuth()


def validate_ml_notification(body: dict[str, Any], application_id: str) -> str | None:
    """Retorna motivo de rejeição ou None. ML não assina notificações: confere application_id/tópico/formato."""
    if not isinstance(body, dict):
        return "corpo deve ser objeto JSON"
    if str(body.get("application_id")) != str(application_id):
        return "application_id não corresponde à aplicação configurada"
    if body.get("topic") not in ML_TOPICS:
        return f"tópico não suportado: {body.get('topic')}"
    paths = {
        "orders_v2": r"/orders/\d+",
        "items": r"/items/MLB\d+",
        "questions": r"/questions/\d+",
        "payments": r"/payments/\d+",
        "shipments": r"/shipments/\d+",
    }
    resource = str(body.get("resource", ""))
    # Simulation uses its own IDs; never allow query strings, traversal, encoded paths or another host.
    pattern = paths[body["topic"]]
    if application_id.startswith("SIM"):
        pattern = pattern.replace(r"\d+", r"[A-Za-z0-9_-]+")
    if not re.fullmatch(pattern, resource):
        return "resource incompatível com o tópico"
    if body.get("user_id") is None:
        return "user_id ausente"
    return None


def ml_delivery_id(body: dict[str, Any]) -> str:
    """Chave de deduplicação: _id quando existir; senão (tópico, recurso, sent). Reenvios mantêm a mesma chave."""
    if body.get("_id"):
        return str(body["_id"])
    return hashlib.sha256(f"{body.get('topic')}|{body.get('resource')}|{body.get('sent')}".encode()).hexdigest()[:40]


# ───────────────────────────── Shopee (NÃO verificado) ─────────────────────────────
SHOPEE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "item_name",
        "description",
        "original_price",
        "seller_stock",
        "weight",
        "dimension",
        "category_id",
        "item_sku",
        "image",
    ],
    "properties": {
        "item_name": {"type": "string", "minLength": 1, "maxLength": 120},
        "description": {"type": "string"},
        "original_price": {"type": "number", "exclusiveMinimum": 0},
        "seller_stock": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["stock"],
                "properties": {"stock": {"type": "integer", "minimum": 0}},
            },
        },
        "weight": {"type": "number", "exclusiveMinimum": 0},
        "dimension": {"type": "object", "required": ["package_length", "package_width", "package_height"]},
        "category_id": {"type": "integer"},
        "item_sku": {"type": "string"},
        "image": {"type": "object", "required": ["image_id_list"]},
    },
}


def to_shopee_item(
    *,
    title: str,
    description: str,
    price_minor: int,
    stock_qty: int,
    weight_g: int,
    dims_mm: tuple[int, int, int],
    category_id: int,
    sku: str,
) -> dict[str, Any]:
    length, width, height = (max(1, round(v / 10)) for v in dims_mm)
    return {
        "item_name": title[:120],
        "description": description or title,
        "original_price": float(_money(price_minor)),
        "seller_stock": [{"stock": max(0, stock_qty)}],
        "weight": round(weight_g / 1000, 3),
        "dimension": {"package_length": length, "package_width": width, "package_height": height},
        "category_id": category_id,
        "item_sku": sku,
        "image": {"image_id_list": []},
    }


def shopee_sign(
    partner_id: int, path: str, timestamp: int, partner_key: str, access_token: str = "", shop_id: int | None = None
) -> str:
    """v2: hex(HMAC-SHA256(partner_id+path+timestamp[+access_token+shop_id], partner_key)). Fonte não oficial."""
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id if shop_id is not None else ''}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


# ───────────────────────────── TikTok Shop (NÃO verificado) ─────────────────────────────
TTS_PRODUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "description", "category_id", "main_images", "skus", "package_weight"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 255},
        "description": {"type": "string"},
        "category_id": {"type": "string"},
        "main_images": {"type": "array"},
        "skus": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["seller_sku", "price", "inventory"],
                "properties": {
                    "seller_sku": {"type": "string"},
                    "price": {
                        "type": "object",
                        "required": ["amount", "currency"],
                        "properties": {"amount": {"type": "string"}, "currency": {"const": "BRL"}},
                    },
                    "inventory": {"type": "array"},
                },
            },
        },
        "package_weight": {"type": "object", "required": ["value", "unit"]},
    },
}


def to_tts_product(
    *,
    title: str,
    description: str,
    category_id: str,
    price_minor: int,
    stock_qty: int,
    sku: str,
    weight_g: int,
    warehouse_id: str = "SIM-WH-BR",
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description or title,
        "category_id": category_id,
        "main_images": [],
        "skus": [
            {
                "seller_sku": sku,
                "price": {"amount": str(_money(price_minor)), "currency": "BRL"},
                "inventory": [{"warehouse_id": warehouse_id, "quantity": max(0, stock_qty)}],
            }
        ],
        "package_weight": {"value": str(round(weight_g / 1000, 3)), "unit": "KILOGRAM"},
    }


def tts_sign(app_secret: str, path: str, params: dict[str, str], body: str = "") -> str:
    """Assinatura do Partner API conforme conhecimento prévio (NÃO conferida): HMAC-SHA256 hex de
    secret + path + concat(chave+valor ordenados, exceto sign/access_token) + body + secret."""
    concat = "".join(f"{k}{params[k]}" for k in sorted(params) if k not in ("sign", "access_token"))
    msg = f"{app_secret}{path}{concat}{body}{app_secret}"
    return hmac.new(app_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


SCHEMAS = {"MERCADO_LIVRE": ML_ITEM_SCHEMA, "SHOPEE": SHOPEE_ITEM_SCHEMA, "TIKTOK_SHOP": TTS_PRODUCT_SCHEMA}


@lru_cache
def _validator(channel: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(SCHEMAS[channel])


def validate_payload(channel: str, payload: dict[str, Any]) -> list[str]:
    return [f"{'/'.join(map(str, e.path)) or '(raiz)'}: {e.message}" for e in _validator(channel).iter_errors(payload)]


class MockMarketplaceProvider(MockCommerceProvider):
    """Mock por canal que rejeita payload fora do contrato (como o canal real rejeitaria)."""

    def __init__(self, channel: str) -> None:
        super().__init__(channel)
        self.channel = channel

    def execute(self, cmd: CommandEnvelope) -> ProviderResult:
        if cmd.operation in ("publish_listing", "update_listing"):
            body = {k: v for k, v in cmd.payload.items() if not k.startswith("_")}
            errors = validate_payload(self.channel, body)
            if errors:
                return ProviderResult(
                    ResultStatus.FAILED,
                    error_kind=ErrorKind.VALIDATION,
                    detail={"reason": f"payload fora do contrato {self.channel}: " + "; ".join(errors[:5])},
                )
        return super().execute(cmd)


class _MLHTTPError(Exception):
    def __init__(self, status: int, data: Any) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.data = status, data


class MLRestProvider:
    """Cliente REAL do Mercado Livre (Items API). Só envia em LIVE, com conta real conectada por OAuth E o gate CH-ML
    aprovado pelo proprietário; fora disso a capacidade não é SUPPORTED e o executor falha tipado (nunca finge).

    Idempotência: o SKU vai em `seller_custom_field` e no atributo SELLER_SKU. Antes de criar, procura o item do
    vendedor pelo SKU — se já existe (retry/replay depois de timeout), reconhece em vez de duplicar. Atualização só
    envia campos que o ML aceita mudar num item ativo (preço, estoque, fotos, atributos); pausa = status "paused".
    Contrato: subconjunto conferido em exemplos oficiais (VERIFIED_SUBSET); a homologação CH-ML valida de fato."""

    name = "mercadolivre-rest"
    ROUTES: ClassVar[dict[str, tuple[str, str]]] = {
        "publish_listing": ("POST", "/items"),
        "update_listing": ("PUT", "/items/{external_id}"),
        "pause_listing": ("PUT", "/items/{external_id}"),
    }
    GATE = "CH-ML"
    UPDATABLE = ("price", "available_quantity", "pictures", "attributes")
    LIVE_STATUSES = ("active", "paused", "under_review", "not_yet_active", "payment_required")

    def __init__(
        self,
        *,
        gate_approved: bool = False,
        connected: bool = False,
        token_source: Callable[[], tuple[str, str]] | None = None,
        http: Callable[..., tuple[int, Any]] | None = None,
        timeout: float = 30.0,
    ) -> None:
        from aicommerce.providers.woocommerce import _http

        self.gate_approved, self.connected = gate_approved, connected and token_source is not None
        self._token_source = token_source  # () -> (access_token, user_id); renova antes de expirar
        self._http = http or _http
        self._timeout = timeout

    def capability(self, operation: str, mode: str) -> Capability:
        if operation not in self.ROUTES or mode == "DRY_RUN" or not self.connected:
            return Capability.UNAVAILABLE
        if not self.gate_approved:
            return Capability.BLOCKED_BY_POLICY
        return Capability.SUPPORTED

    def build_request(self, cmd: CommandEnvelope, access_token: str, external_id: str | None = None) -> dict[str, Any]:
        method, path = self.ROUTES[cmd.operation]
        body = {k: v for k, v in cmd.payload.items() if not k.startswith("_")}
        if cmd.operation == "pause_listing":
            body = {"status": "paused"}
        elif cmd.operation == "update_listing":
            body = {k: body[k] for k in self.UPDATABLE if k in body}
        return {
            "method": method,
            "url": ML_API + path.format(external_id=external_id or "{id}"),
            "json": body,
            "headers": {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Request-ID": cmd.request_id,
            },
        }

    # ── HTTP ──
    def _call(self, token: str, method: str, path: str, body: dict[str, Any] | None = None, rid: str = ""):
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
        if rid:
            headers["X-Request-ID"] = rid
        status, data = self._http(method, ML_API + path, headers, body, self._timeout)
        if (
            method in ("POST", "PUT")
            and status in (200, 201)
            and (not isinstance(data, dict) or not re.fullmatch(r"MLB\d+", str(data.get("id", ""))))
        ):
            return 502, {"message": "resposta de escrita fora do contrato; efeito incerto"}
        return status, data

    def _find_by_sku(self, token: str, user_id: str, sku: str) -> dict[str, Any] | None:
        from urllib.parse import quote

        status, data = self._call(token, "GET", f"/users/{user_id}/items/search?seller_sku={quote(sku)}")
        if status != 200:
            raise _MLHTTPError(status, data)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise _MLHTTPError(502, {"message": "resposta de busca fora do contrato"})
        if (data.get("paging") or {}).get("total", len(data["results"])) > len(data["results"]):
            raise _MLHTTPError(409, {"message": "busca incompleta; reconciliação manual necessária"})
        matches = []
        for item_id in data["results"]:
            if not re.fullmatch(r"MLB\d+", str(item_id)):
                raise _MLHTTPError(502, {"message": "identificador de item inválido"})
            st, item = self._call(token, "GET", f"/items/{item_id}")
            if st != 200:
                # Absence cannot be inferred from a failed read: publishing here could duplicate an item.
                raise _MLHTTPError(st, item)
            if not isinstance(item, dict) or str(item.get("id")) != str(item_id):
                raise _MLHTTPError(502, {"message": "resposta de item fora do contrato"})
            if item.get("seller_id") is not None and str(item["seller_id"]) != user_id:
                raise _MLHTTPError(403, {"message": "item de outro vendedor"})
            if item.get("status") in self.LIVE_STATUSES:
                matches.append(item)
        if len(matches) > 1:
            raise _MLHTTPError(409, {"message": "SKU ambíguo; não alterar nem duplicar anúncios"})
        return matches[0] if matches else None

    @staticmethod
    def _failed(status: int, data: Any, op: str) -> ProviderResult:
        data = data if isinstance(data, dict) else {}
        causes = [str(c.get("message") or c.get("code")) for c in data.get("cause") or [] if isinstance(c, dict)]
        if status in (408, 429) or status >= 500:
            kind = ErrorKind.TRANSIENT
        elif status == 401:
            kind = ErrorKind.TOKEN_EXPIRED  # token recusado mesmo renovado: reconectar a conta
        elif status == 400:
            kind = ErrorKind.VALIDATION
        else:
            kind = ErrorKind.REJECTED
        reason = "; ".join([str(data.get("message") or data.get("error") or ""), *causes[:4]]).strip("; ")
        return ProviderResult(
            ResultStatus.FAILED, error_kind=kind, detail={"http": status, "reason": reason[:400], "op": op}
        )

    @classmethod
    def _write_failure(cls, status: int, data: Any, op: str) -> ProviderResult:
        if status == 408 or status >= 500:
            return ProviderResult(
                ResultStatus.UNKNOWN,
                error_kind=ErrorKind.TRANSIENT,
                detail={"http": status, "op": op, "reason": "efeito incerto; reconciliar antes de repetir"},
            )
        return cls._failed(status, data, op)

    def execute(self, cmd: CommandEnvelope) -> ProviderResult:
        cap = self.capability(cmd.operation, cmd.mode)
        if cap is not Capability.SUPPORTED:
            return ProviderResult(
                ResultStatus.FAILED,
                error_kind=ErrorKind.UNSUPPORTED if cap is Capability.UNAVAILABLE else ErrorKind.POLICY_DENIED,
                detail={
                    "reason": "Mercado Livre real sem conta conectada (OAuth) ou fora de LIVE"
                    if cap is Capability.UNAVAILABLE
                    else "gate CH-ML não aprovado"
                },
            )
        assert self._token_source is not None
        try:
            token, user_id = self._token_source()
        except Exception as e:  # conta precisa ser reconectada (refresh recusado) ou ML fora do ar na renovação
            kind = ErrorKind.TRANSIENT if getattr(e, "transient", False) else ErrorKind.TOKEN_EXPIRED
            return ProviderResult(ResultStatus.FAILED, error_kind=kind, detail={"reason": str(e)[:300]})
        body = {k: v for k, v in cmd.payload.items() if not k.startswith("_")}
        sent = False
        try:
            if cmd.operation == "pause_listing":
                ext = str(cmd.payload.get("external_id") or "")
                if not re.fullmatch(r"MLB\d+", ext):
                    return ProviderResult(
                        ResultStatus.FAILED, error_kind=ErrorKind.VALIDATION, detail={"reason": "pausa sem external_id"}
                    )
                sent = True
                status, data = self._call(token, "PUT", f"/items/{ext}", {"status": "paused"}, cmd.request_id)
                if status == 200:
                    return ProviderResult(
                        ResultStatus.SUCCEEDED,
                        provider_ref=str(data.get("id", ext)),
                        detail={"status": data.get("status")},
                    )
                return self._write_failure(status, data, cmd.operation)
            errors = validate_payload("MERCADO_LIVRE", body)
            if str(body.get("category_id", "")).startswith("SIM-"):
                errors.append("category_id: categoria simulada — mapeie a categoria real do ML antes de publicar")
            if errors:
                return ProviderResult(
                    ResultStatus.FAILED,
                    error_kind=ErrorKind.VALIDATION,
                    detail={"reason": "payload fora do contrato Mercado Livre: " + "; ".join(errors[:5])},
                )
            sku = str(body.get("seller_custom_field") or "")
            existing = self._find_by_sku(token, user_id, sku) if sku else None
            if cmd.operation == "publish_listing":
                if existing is not None:  # já criado (retry/replay depois de timeout): não duplica
                    return ProviderResult(
                        ResultStatus.SUCCEEDED,
                        provider_ref=str(existing["id"]),
                        detail={"replayed": True, "status": existing.get("status")},
                    )
                sent = True
                status, data = self._call(token, "POST", "/items", body, cmd.request_id)
                if status in (200, 201):
                    return ProviderResult(
                        ResultStatus.SUCCEEDED,
                        provider_ref=str(data["id"]),
                        detail={"status": data.get("status"), "permalink": data.get("permalink")},
                    )
                return self._write_failure(status, data, cmd.operation)
            if cmd.operation == "update_listing":
                if existing is None:
                    return ProviderResult(
                        ResultStatus.FAILED,
                        error_kind=ErrorKind.REJECTED,
                        detail={"reason": f"item com SKU {sku} não encontrado no ML (encerrado manualmente?)"},
                    )
                patch = {k: body[k] for k in self.UPDATABLE if k in body}
                sent = True
                status, data = self._call(token, "PUT", f"/items/{existing['id']}", patch, cmd.request_id)
                if status == 200:
                    return ProviderResult(
                        ResultStatus.SUCCEEDED, provider_ref=str(data["id"]), detail={"status": data.get("status")}
                    )
                return self._write_failure(status, data, cmd.operation)
        except _MLHTTPError as e:
            return self._failed(e.status, e.data, cmd.operation)
        except (TimeoutError, OSError) as e:
            # depois do envio não dá para saber se o ML aplicou: UNKNOWN → reconciliação consulta antes de repetir
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
        """Reconciliação: o efeito existe no ML? Pausa = item pausado; publicação = item do vendedor com o SKU;
        atualização = item com o preço/estoque pedidos (senão reaplicar o PUT é seguro)."""
        if not self.connected or not payload or self._token_source is None:
            return None
        try:
            token, user_id = self._token_source()
            if payload.get("external_id") and set(payload) <= {"channel", "external_id", "reason"}:
                status, data = self._call(token, "GET", f"/items/{payload['external_id']}")
                if status == 200 and data.get("status") == "paused":
                    return ProviderResult(
                        ResultStatus.SUCCEEDED, provider_ref=str(data["id"]), detail={"reconciled": True}
                    )
                return None
            found = self._find_by_sku(token, user_id, str(payload.get("seller_custom_field") or ""))
        except Exception:  # consulta falhou: "não confirmado"; publicar de novo procura pelo SKU antes de criar
            return None
        if found is None:
            return None
        if idempotency_key.endswith(":update_listing_material") and any(
            k in payload and found.get(k) != payload[k] for k in self.UPDATABLE
        ):
            return None
        return ProviderResult(ResultStatus.SUCCEEDED, provider_ref=str(found["id"]), detail={"reconciled": True})
