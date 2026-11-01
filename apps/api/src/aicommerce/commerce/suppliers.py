"""Dossiê e avaliação de fornecedores (SDD FR-006, gate SUP-01).

A avaliação é determinística (sem LLM): checklist das evidências do SUP-01 + risco calculado.
Aprovar um fornecedor aqui é uma decisão INTERNA de catálogo; o gate SUP-01 de go-live continua separado
e exige evidência real registrada em certification_gates.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.governance import audit
from aicommerce.models import Supplier, SupplierDocument, SupplierOffer
from aicommerce.office import events

STOCK_FRESHNESS = dt.timedelta(hours=24)
DOC_TYPES = {
    "CNPJ_CARD",
    "FOREIGN_REGISTRATION",
    "CONTRACT",
    "INVOICE_SAMPLE",
    "CERTIFICATE",
    "PRODUCT_PHOTO",
    "SLA_TERMS",
    "OTHER",
}
STATUSES = {"PROSPECT", "EVALUATING", "APPROVED", "SUSPENDED", "REJECTED"}
TRANSITIONS = {
    "PROSPECT": {"EVALUATING", "REJECTED"},
    "EVALUATING": {"APPROVED", "REJECTED", "PROSPECT"},
    "APPROVED": {"SUSPENDED"},
    "SUSPENDED": {"EVALUATING", "REJECTED"},
    "REJECTED": {"EVALUATING"},
}
SAMPLE_STATUSES = {"NOT_REQUESTED", "REQUESTED", "RECEIVED", "APPROVED", "REJECTED"}


class SupplierError(Exception):
    code = "SUPPLIER_ERROR"
    http = 409


class VersionConflict(SupplierError):
    code = "VERSION_CONFLICT"


class ChecklistIncomplete(SupplierError):
    code = "CHECKLIST_INCOMPLETE"


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Assessment:
    score: int  # 0 (sem risco aparente) … 100 (risco máximo)
    level: str
    checklist: list[ChecklistItem]
    ready_for_approval: bool
    stale_offers: int

    def as_dict(self) -> dict:
        return asdict(self)


def _verified(docs: list[SupplierDocument], *types: str, now: dt.datetime) -> bool:
    return any(
        d.doc_type in types and d.verified_at is not None and (d.expires_at is None or d.expires_at > now) for d in docs
    )


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _check_digit(digits: str, weights: list[int]) -> int:
    r = sum(int(a) * b for a, b in zip(digits, weights, strict=True)) % 11
    return 0 if r < 2 else 11 - r


def valid_brazilian_id(value: str | None) -> bool:
    """CNPJ (14 dígitos) ou CPF (11) com dígitos verificadores corretos — checagem determinística, sem rede. Não
    prova que a empresa existe nem que está ativa (isso é a consulta oficial/documento verificado)."""
    d = _digits(value or "")
    if len(d) not in (11, 14) or len(set(d)) == 1:
        return False
    if len(d) == 14:
        w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        return _check_digit(d[:12], w1) == int(d[12]) and _check_digit(d[:13], [6, *w1]) == int(d[13])
    return _check_digit(d[:9], list(range(10, 1, -1))) == int(d[9]) and _check_digit(
        d[:10], list(range(11, 1, -1))
    ) == int(d[10])


def assess(
    s: Supplier, docs: list[SupplierDocument], offers: list[SupplierOffer], now: dt.datetime | None = None
) -> Assessment:
    now = now or dt.datetime.now(dt.UTC)
    active = [o for o in offers if o.status == "ACTIVE" and (o.valid_until is None or o.valid_until > now)]
    fresh = [o for o in active if now - o.stock_observed_at <= STOCK_FRESHNESS]
    stale = len(active) - len(fresh)
    id_types = ("CNPJ_CARD",) if s.origin == "NATIONAL" else ("FOREIGN_REGISTRATION",)
    # fornecedor real nacional: CNPJ/CPF com dígito verificador válido (simulado usa identificador fictício)
    id_ok = bool(s.legal_id) and (s.is_simulated or s.origin != "NATIONAL" or valid_brazilian_id(s.legal_id))
    items = [
        ChecklistItem(
            "identificacao",
            "Identificação (registro legal verificado)",
            id_ok and _verified(docs, *id_types, now=now),
            "CNPJ/CPF com dígito verificador inválido"
            if s.legal_id and not id_ok
            else "documento verificado"
            if _verified(docs, *id_types, now=now)
            else "falta registro verificado",
        ),
        ChecklistItem("contrato", "Contrato vigente", _verified(docs, "CONTRACT", now=now), "tipo CONTRACT verificado"),
        ChecklistItem(
            "sla",
            "SLA de expedição definido",
            s.sla_dispatch_days is not None and s.sla_dispatch_days <= 5,
            f"{s.sla_dispatch_days} dia(s)" if s.sla_dispatch_days is not None else "sem SLA",
        ),
        ChecklistItem(
            "estoque",
            "Estoque observado nas últimas 24 h",
            bool(fresh),
            f"{len(fresh)} oferta(s) com estoque recente, {stale} obsoleta(s)",
        ),
        ChecklistItem(
            "documentacao",
            "Documentação fiscal/produto",
            _verified(docs, "INVOICE_SAMPLE", "CERTIFICATE", now=now),
            "nota de amostra ou certificado",
        ),
        ChecklistItem("amostra", "Amostra aprovada", s.sample_status == "APPROVED", f"amostra: {s.sample_status}"),
    ]
    missing = sum(not i.ok for i in items)
    score = min(
        100,
        missing * 14
        + (10 if s.origin == "INTERNATIONAL" else 0)
        + min(stale, 3) * 5
        + (10 if (s.sla_dispatch_days or 99) > 3 else 0),
    )
    level = "ALTO" if score >= 50 else "MÉDIO" if score >= 20 else "BAIXO"
    return Assessment(score, level, items, missing == 0, stale)


def load(db: Session, supplier_id) -> tuple[Supplier, list[SupplierDocument], list[SupplierOffer]]:
    s = db.get(Supplier, supplier_id)
    if s is None:
        raise SupplierError("fornecedor inexistente")
    docs = list(db.execute(select(SupplierDocument).where(SupplierDocument.supplier_id == s.id)).scalars())
    offers = list(db.execute(select(SupplierOffer).where(SupplierOffer.supplier_id == s.id)).scalars())
    return s, docs, offers


def transition(
    db: Session, supplier_id, *, to: str, expected_version: int, actor: str, reason: str, can_approve: bool
) -> Supplier:
    s = db.get(Supplier, supplier_id, with_for_update=True)
    if s is None:
        raise SupplierError("fornecedor inexistente")
    if s.version != expected_version:
        raise VersionConflict(f"versão atual {s.version} ≠ {expected_version}; recarregue o dossiê")
    if to not in TRANSITIONS.get(s.status, set()):
        raise SupplierError(f"transição {s.status} → {to} não permitida")
    if to == "APPROVED":
        if not can_approve:
            raise SupplierError("aprovar fornecedor exige suppliers:approve")
        _, docs, offers = load(db, s.id)
        a = assess(s, docs, offers)
        if not a.ready_for_approval:
            missing = ", ".join(i.label for i in a.checklist if not i.ok)
            raise ChecklistIncomplete(f"checklist incompleto: {missing}")
    before = s.status
    s.status, s.version = to, s.version + 1
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="supplier.status_changed",
        subject_type="supplier",
        subject_id=str(s.id),
        payload={"from": before, "to": to, "reason": reason},
    )
    events.emit(
        db,
        "supplier.status_changed",
        agent_id="nexus",
        payload={"supplier_id": str(s.id), "name": s.name, "from": before, "to": to, "simulated": s.is_simulated},
    )
    return s


def flag_stale_stock(db: Session, offers: list[SupplierOffer], now: dt.datetime | None = None) -> list[str]:
    """Emite supplier.stock.stale para ofertas ativas sem observação recente de estoque."""
    now = now or dt.datetime.now(dt.UTC)
    stale = [o for o in offers if o.status == "ACTIVE" and now - o.stock_observed_at > STOCK_FRESHNESS]
    for o in stale:
        events.emit(
            db,
            "supplier.stock.stale",
            agent_id="nexus",
            payload={
                "offer_id": str(o.id),
                "supplier_id": str(o.supplier_id),
                "observed_at": o.stock_observed_at.isoformat(),
            },
        )
    return [str(o.id) for o in stale]
