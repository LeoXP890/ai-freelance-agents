"""Contratos de adaptadores externos (SDD §9, §23.1, REQ-DEV-005).

Todo comando externo recebe request_id, idempotency_key, mode, capability, approval_token_ref e
expected_version; toda resposta expõe status, provider_ref, observed_at e error_kind.
Recurso indisponível retorna UNSUPPORTED tipado — jamais sucesso simulado.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field
from typing import Any, Protocol


class Capability(enum.StrEnum):
    SUPPORTED = "SUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"


class ResultStatus(enum.StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # timeout/queda após envio: o efeito pode ou não ter ocorrido


class ErrorKind(enum.StrEnum):
    TIMEOUT = "TIMEOUT"
    TRANSIENT = "TRANSIENT"
    REJECTED = "REJECTED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    VALIDATION = "VALIDATION"
    UNSUPPORTED = "UNSUPPORTED"
    POLICY_DENIED = "POLICY_DENIED"
    AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    RESERVATION_INVALID = "RESERVATION_INVALID"


# Erros após os quais repetir não muda o resultado.
PERMANENT_ERRORS = {
    ErrorKind.REJECTED,
    ErrorKind.TOKEN_EXPIRED,
    ErrorKind.VALIDATION,
    ErrorKind.UNSUPPORTED,
    ErrorKind.POLICY_DENIED,
    ErrorKind.AUTHORIZATION_INVALID,
    ErrorKind.DEADLINE_EXCEEDED,
    ErrorKind.RESERVATION_INVALID,
}


@dataclass(frozen=True)
class CommandEnvelope:
    request_id: str
    idempotency_key: str
    mode: str
    capability: Capability
    approval_token_ref: str
    expected_version: int
    operation: str
    payload: dict[str, Any]
    attempt: int = 1


@dataclass(frozen=True)
class ProviderResult:
    status: ResultStatus
    provider_ref: str | None = None
    observed_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    error_kind: ErrorKind | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class ExternalProvider(Protocol):
    """Base comum: executar um comando e consultar pelo idempotency_key (reconciliação)."""

    name: str

    def capability(self, operation: str, mode: str) -> Capability: ...

    def execute(self, cmd: CommandEnvelope) -> ProviderResult: ...

    def lookup(self, idempotency_key: str, payload: dict[str, Any] | None = None) -> ProviderResult | None: ...


class CommerceProvider(ExternalProvider, Protocol):
    """WooCommerce, Mercado Livre, Shopee, TikTok Shop. Operações: publish_listing, update_listing, pause_listing."""


class AdsProvider(ExternalProvider, Protocol):
    """Plataformas de mídia. Operações: create_campaign, increase_budget, pause_campaign."""


class SupplierProvider(ExternalProvider, Protocol):
    """Pedido de compra ao fornecedor (place_order) e consulta de estoque."""


class FulfillmentProvider(Protocol):
    """Rastreio de envios: status e eventos da transportadora/fornecedor."""

    def track(self, tracking_code: str) -> dict[str, Any]: ...


class PaymentReader(Protocol):
    """Leitura de pagamentos/repasses do canal (sem movimentar dinheiro)."""

    def fetch_settlements(self, channel: str, since: dt.datetime) -> list[dict[str, Any]]: ...


class FiscalDocumentProvider(Protocol):
    """Emissão de documento fiscal por provedor autorizado (SDD §10)."""

    environment: str

    def issue(self, order_ref: str, items: list[dict[str, Any]]) -> dict[str, Any]: ...
