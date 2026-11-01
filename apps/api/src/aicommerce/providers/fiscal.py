"""Emissão fiscal (COM-011). Somente mock em DRY_RUN: documentos FICTÍCIOS, sem valor fiscal.

Regras determinísticas (SDD §10): sem NCM em algum item → FISCAL_HOLD; em LIVE não há modelo fiscal aprovado nem
provedor credenciado → FISCAL_HOLD sempre. Nunca inventa CFOP/CST: o mock não calcula tributo, só simula o ciclo.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aicommerce.canonical import sha256_of

SIM_ENVIRONMENT = "SIMULACAO_SEM_VALOR_FISCAL"


class MockFiscalProvider:
    name = "mock-fiscal"
    environment = SIM_ENVIRONMENT

    def issue(self, order_ref: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        missing = [i["sku"] for i in items if not i.get("ncm")]
        if missing:
            return {"status": "FISCAL_HOLD", "reason": f"NCM ausente para {', '.join(missing)}"}
        digits = str(int(sha256_of(order_ref), 16))[:36]
        return {
            "status": "AUTHORIZED_SIMULATED",
            "access_key": f"SIM{digits}"[:44].ljust(44, "0"),
            "issued_at": dt.datetime.now(dt.UTC),
            "reason": "Documento FICTÍCIO de simulação — sem valor fiscal",
        }


def provider_for(mode: str) -> MockFiscalProvider | None:
    return MockFiscalProvider() if mode == "DRY_RUN" else None
