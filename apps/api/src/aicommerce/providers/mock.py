"""Provedores Mock* (DRY_RUN): determinísticos, schema compatível, falhas injetáveis.

O "mundo externo" simulado fica em `sim_provider_effects`, gravado em transação própria (como um sistema
remoto faria), para que 'timeout após o efeito' seja reconciliável por outro processo.

Injeção de falhas (somente DRY_RUN) pela chave `_simulate` do payload:
  transient_once   falha transitória na 1ª tentativa, depois sucesso
  fail             falha transitória sempre
  reject           rejeição permanente do provedor
  token_expired    credencial expirada (permanente)
  timeout_before   timeout sem efeito na 1ª tentativa
  timeout_after    efeito aplicado e resposta perdida na 1ª tentativa (resultado UNKNOWN)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aicommerce.canonical import sha256_of
from aicommerce.db import session_scope
from aicommerce.models import SimProviderEffect
from aicommerce.providers.contracts import Capability, CommandEnvelope, ErrorKind, ProviderResult, ResultStatus

FAULTS = {"transient_once", "fail", "reject", "token_expired", "timeout_before", "timeout_after"}


class _MockBase:
    name = "mock"
    operations: frozenset[str] = frozenset()

    def capability(self, operation: str, mode: str) -> Capability:
        if mode != "DRY_RUN":
            return Capability.UNAVAILABLE  # mock nunca atende LIVE
        return Capability.SUPPORTED if operation in self.operations else Capability.UNAVAILABLE

    def _ref(self, key: str) -> str:
        return f"SIM-{self.name}-{sha256_of(key)[:12]}"

    def _apply(self, cmd: CommandEnvelope) -> str:
        """Aplica o efeito de forma idempotente por (provider, idempotency_key)."""
        ref = self._ref(cmd.idempotency_key)
        with session_scope() as db:
            exists = db.execute(
                select(SimProviderEffect).where(
                    SimProviderEffect.provider == self.name, SimProviderEffect.idempotency_key == cmd.idempotency_key
                )
            ).scalar_one_or_none()
            if exists:
                return exists.provider_ref
            db.add(
                SimProviderEffect(
                    provider=self.name,
                    operation=cmd.operation,
                    idempotency_key=cmd.idempotency_key,
                    provider_ref=ref,
                    request={k: v for k, v in cmd.payload.items() if not k.startswith("_")},
                )
            )
            try:
                db.flush()
            except IntegrityError:  # corrida entre chamadas com a mesma chave: provedor honra idempotência
                db.rollback()
        return ref

    def execute(self, cmd: CommandEnvelope) -> ProviderResult:
        if self.capability(cmd.operation, cmd.mode) is not Capability.SUPPORTED:
            return ProviderResult(
                ResultStatus.FAILED,
                error_kind=ErrorKind.UNSUPPORTED,
                detail={"reason": f"{self.name}.{cmd.operation} indisponível em {cmd.mode}"},
            )
        fault = cmd.payload.get("_simulate")
        first = cmd.attempt == 1
        if fault == "reject":
            return ProviderResult(
                ResultStatus.FAILED,
                error_kind=ErrorKind.REJECTED,
                detail={"reason": "anúncio recusado pela política do canal (simulado)"},
            )
        if fault == "token_expired":
            return ProviderResult(ResultStatus.FAILED, error_kind=ErrorKind.TOKEN_EXPIRED)
        if fault == "fail" or (fault == "transient_once" and first):
            return ProviderResult(ResultStatus.FAILED, error_kind=ErrorKind.TRANSIENT)
        if fault == "timeout_before" and first:
            return ProviderResult(ResultStatus.UNKNOWN, error_kind=ErrorKind.TIMEOUT)
        ref = self._apply(cmd)
        if fault == "timeout_after" and first:
            return ProviderResult(ResultStatus.UNKNOWN, error_kind=ErrorKind.TIMEOUT)
        return ProviderResult(ResultStatus.SUCCEEDED, provider_ref=ref, detail={"simulated": True})

    def lookup(self, idempotency_key: str, payload: dict[str, Any] | None = None) -> ProviderResult | None:
        with session_scope() as db:
            effect = db.execute(
                select(SimProviderEffect).where(
                    SimProviderEffect.provider == self.name, SimProviderEffect.idempotency_key == idempotency_key
                )
            ).scalar_one_or_none()
            if effect is None:
                return None
            return ProviderResult(
                ResultStatus.SUCCEEDED, provider_ref=effect.provider_ref, detail={"simulated": True, "reconciled": True}
            )


class MockCommerceProvider(_MockBase):
    operations = frozenset({"publish_listing", "update_listing", "pause_listing", "refund_order"})

    def __init__(self, channel: str = "WOOCOMMERCE"):
        self.name = f"mock-commerce-{channel.lower()}"


class MockAdsProvider(_MockBase):
    name = "mock-ads"
    operations = frozenset({"create_campaign", "increase_budget", "pause_campaign"})


class MockSupplierProvider(_MockBase):
    """Fornecedor simulado: aceita pedidos de compra (place_order) de forma idempotente."""

    name = "mock-supplier"
    operations = frozenset({"place_order"})
