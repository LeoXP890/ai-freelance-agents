"""Pagamento ao fornecedor: saldo liberado da operação primeiro, fundo de reserva depois (ADR 0008).

Não é o orçamento de `ledger.py`: aqui só conta dinheiro observado como liberado e transferível em cada fonte.

    livre(fonte) = última leitura fresca − comprometido − piso
    comprometido = pernas de alocações RESERVED/PAYMENT_PENDING/PAYMENT_UNKNOWN
                 + pernas PAID confirmadas depois da leitura (ainda não refletidas no saldo observado)

* Saldo retido/a liberar é registrado na leitura, mas nunca entra no cálculo.
* As fontes envolvidas são travadas (SELECT … FOR UPDATE, ordem fixa): alocações concorrentes são serializadas e
  o índice único parcial impede duas alocações vivas para a mesma obrigação.
* Sem saldo e limite para o total → BLOCKED (pendência registrada), nunca pagamento parcial.
* Uma única obrigação ao fornecedor por alocação. Resultado incerto vira PAYMENT_UNKNOWN e mantém o dinheiro
  comprometido até a consulta ao provedor; nenhuma nova tentativa antes disso.
* Recebível liberado depois abate a exposição da reserva contabilmente; nunca gera segundo pagamento nem presume
  transferência de recomposição (o saldo real da reserva continua vindo das leituras).

Somente DRY_RUN nesta etapa: não existe provedor de débito homologado; fora de DRY_RUN alocar/pagar é recusado.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import audit, killswitch
from aicommerce.models import (
    ApprovalProposal,
    CashBalanceSnapshot,
    CashSource,
    Fulfillment,
    FundingAllocation,
    FundingEvent,
    FundingLeg,
    Order,
    OrderItem,
    Supplier,
    SupplierOffer,
)

KINDS = ("OPERATING", "RESERVE")
ORIGINS = ("SIMULATED", "STATEMENT_IMPORT", "PROVIDER_API")
HELD = ("RESERVED", "PAYMENT_PENDING", "PAYMENT_UNKNOWN")
LIVE = (*HELD, "PAID")
OUTCOMES = ("SUCCEEDED", "FAILED", "UNKNOWN")
CLOCK_SKEW = dt.timedelta(seconds=60)


class FundingError(Exception):
    code = "FUNDING_ERROR"


class FundingDisabled(FundingError):
    code = "FUNDING_DISABLED"


class IdempotencyConflict(FundingError):
    code = "IDEMPOTENCY_CONFLICT"


class InvalidAllocationState(FundingError):
    code = "INVALID_ALLOCATION_STATE"


class PaymentOutcomeUnknown(FundingError):
    code = "PAYMENT_OUTCOME_UNKNOWN"


class PaymentBlocked(FundingError):
    code = "PAYMENT_BLOCKED"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SourceView:
    source_id: str
    code: str
    kind: str
    currency: str
    configured: bool
    snapshot_id: int | None
    observed_at: str | None
    stale: bool
    available_minor: int | None
    retained_minor: int | None
    committed_minor: int
    floor_minor: int | None
    free_minor: int | None  # None = sem leitura fresca ou sem política: não financia
    exposure_minor: int | None  # só reserva: pago/comprometido ainda não recuperado
    period_used_minor: int | None
    simulated: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _require_dry_run() -> None:
    if not get_settings().is_dry_run:
        raise FundingDisabled("débito ao fornecedor sem provedor homologado: somente DRY_RUN")


def _audit(db: Session, actor: str, action: str, subject_id: str, payload: dict) -> None:
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action=action,
        subject_type="funding_allocation",
        subject_id=subject_id,
        payload=payload,
    )


def _event(
    db: Session,
    alloc: FundingAllocation,
    event_type: str,
    key: str,
    *,
    actor: str,
    amount_minor: int | None = None,
    external_ref: str | None = None,
    payload: dict | None = None,
) -> FundingEvent:
    evt = FundingEvent(
        allocation_id=alloc.id,
        event_type=event_type,
        amount_minor=amount_minor,
        external_ref=external_ref,
        idempotency_key=key,
        payload=payload or {},
        created_by=actor,
    )
    db.add(evt)
    db.flush()
    return evt


# ───────────────────────────── Fontes e leituras ─────────────────────────────
def ensure_source(db: Session, code: str, kind: str, name: str, currency: str = "BRL") -> CashSource:
    if kind not in KINDS:
        raise FundingError(f"tipo de fonte inválido: {kind}")
    src = db.execute(select(CashSource).where(CashSource.code == code)).scalar_one_or_none()
    if src:
        if src.kind != kind or src.currency != currency:
            raise FundingError("fonte existente com tipo/moeda diferentes")
        return src
    src = CashSource(
        code=code, kind=kind, name=name, currency=currency, is_simulated=get_settings().is_dry_run, policy_version=0
    )
    db.add(src)
    db.flush()
    return src


def configure_source(
    db: Session,
    source_id: uuid.UUID,
    *,
    floor_minor: int,
    approved_by: str,
    per_order_limit_minor: int | None = None,
    period_limit_minor: int | None = None,
    period_days: int | None = None,
    max_exposure_minor: int | None = None,
) -> CashSource:
    """Política aprovada pelo proprietário. Reserva exige piso e todos os limites; nenhum valor é presumido."""
    src = db.get(CashSource, source_id, with_for_update=True)
    if src is None:
        raise FundingError("fonte inexistente")
    if floor_minor is None or floor_minor < 0:
        raise FundingError("piso obrigatório e não negativo")
    limits = (per_order_limit_minor, period_limit_minor, period_days, max_exposure_minor)
    if any(v is not None and v <= 0 for v in limits):
        raise FundingError("limites devem ser positivos")
    if (period_limit_minor is None) != (period_days is None):
        raise FundingError("limite por período exige valor e duração")
    if src.kind == "RESERVE" and any(v is None for v in limits):
        raise FundingError("reserva exige limite por pedido, por período e de exposição total")
    before = {
        "floor": src.floor_minor,
        "per_order": src.per_order_limit_minor,
        "period": src.period_limit_minor,
        "period_days": src.period_days,
        "max_exposure": src.max_exposure_minor,
        "version": src.policy_version,
    }
    src.floor_minor, src.per_order_limit_minor = floor_minor, per_order_limit_minor
    src.period_limit_minor, src.period_days, src.max_exposure_minor = (
        period_limit_minor,
        period_days,
        max_exposure_minor,
    )
    src.policy_approved_by, src.policy_version = approved_by, src.policy_version + 1
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=approved_by,
        action="funding.source.configured",
        subject_type="cash_source",
        subject_id=str(src.id),
        payload={
            "before": before,
            "after": {
                "floor": floor_minor,
                "per_order": per_order_limit_minor,
                "period": period_limit_minor,
                "period_days": period_days,
                "max_exposure": max_exposure_minor,
                "version": src.policy_version,
            },
        },
    )
    return src


def record_snapshot(
    db: Session,
    source_id: uuid.UUID,
    *,
    available_minor: int,
    currency: str,
    observed_at: dt.datetime,
    origin: str,
    idempotency_key: str,
    actor: str,
    retained_minor: int = 0,
    external_ref: str | None = None,
) -> tuple[CashBalanceSnapshot, bool]:
    """Registra leitura de saldo. Simulada só em fonte simulada; leitura real nunca em fonte simulada."""
    if available_minor < 0 or retained_minor < 0:
        raise FundingError("saldos não podem ser negativos")
    if origin not in ORIGINS:
        raise FundingError(f"origem inválida: {origin}")
    if observed_at.tzinfo is None:
        raise FundingError("observed_at precisa de fuso horário")
    src = db.get(CashSource, source_id, with_for_update=True)
    if src is None:
        raise FundingError("fonte inexistente")
    dup = db.execute(
        select(CashBalanceSnapshot).where(CashBalanceSnapshot.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if dup:
        same = (dup.source_id, dup.available_minor, dup.retained_minor, dup.currency, dup.observed_at) == (
            source_id,
            available_minor,
            retained_minor,
            currency,
            observed_at,
        )
        if not same:
            raise IdempotencyConflict("leitura reutilizada com dados diferentes")
        return dup, False
    if currency != src.currency:
        raise FundingError(f"moeda {currency} difere da fonte ({src.currency}); conversão não é feita aqui")
    if (origin == "SIMULATED") != src.is_simulated:
        raise FundingError("origem da leitura incompatível com a fonte (simulada × real)")
    if observed_at > _now() + CLOCK_SKEW:
        raise FundingError("leitura no futuro")
    snap = CashBalanceSnapshot(
        source_id=source_id,
        available_minor=available_minor,
        retained_minor=retained_minor,
        currency=currency,
        observed_at=observed_at,
        origin=origin,
        external_ref=external_ref,
        idempotency_key=idempotency_key,
        recorded_by=actor,
    )
    db.add(snap)
    db.flush()
    return snap, True


def _latest(db: Session, source_id: uuid.UUID) -> CashBalanceSnapshot | None:
    return db.execute(
        select(CashBalanceSnapshot)
        .where(CashBalanceSnapshot.source_id == source_id)
        .order_by(CashBalanceSnapshot.observed_at.desc(), CashBalanceSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _fresh(snap: CashBalanceSnapshot | None, now: dt.datetime) -> bool:
    return snap is not None and now - snap.observed_at <= dt.timedelta(seconds=get_settings().funding_balance_max_age_s)


def _configured(src: CashSource) -> bool:
    if src.policy_approved_by is None or src.floor_minor is None:
        return False
    if src.kind == "RESERVE":
        return None not in (src.per_order_limit_minor, src.period_limit_minor, src.period_days, src.max_exposure_minor)
    return True


def _committed(db: Session, source_id: uuid.UUID, observed_at: dt.datetime | None) -> int:
    paid_after = FundingAllocation.status == "PAID"
    if observed_at is not None:
        paid_after = and_(paid_after, FundingAllocation.paid_at > observed_at)
    total = db.execute(
        select(func.coalesce(func.sum(FundingLeg.amount_minor), 0))
        .join(FundingAllocation, FundingLeg.allocation_id == FundingAllocation.id)
        .where(FundingLeg.source_id == source_id, or_(FundingAllocation.status.in_(HELD), paid_after))
    ).scalar_one()
    return int(total)


def _exposure(db: Session, source_id: uuid.UUID) -> int:
    total = db.execute(
        select(func.coalesce(func.sum(FundingLeg.amount_minor - FundingAllocation.reserve_recovered_minor), 0))
        .join(FundingAllocation, FundingLeg.allocation_id == FundingAllocation.id)
        .where(FundingLeg.source_id == source_id, FundingAllocation.status.in_(LIVE))
    ).scalar_one()
    return int(total)


def _period_used(db: Session, src: CashSource, now: dt.datetime) -> int:
    since = now - dt.timedelta(days=src.period_days or 0)
    total = db.execute(
        select(func.coalesce(func.sum(FundingLeg.amount_minor), 0))
        .join(FundingAllocation, FundingLeg.allocation_id == FundingAllocation.id)
        .where(
            FundingLeg.source_id == src.id,
            FundingAllocation.status.in_(LIVE),
            FundingAllocation.created_at >= since,
        )
    ).scalar_one()
    return int(total)


def _free(db: Session, src: CashSource, snap: CashBalanceSnapshot) -> int:
    return snap.available_minor - _committed(db, src.id, snap.observed_at) - (src.floor_minor or 0)


def source_view(db: Session, source_id: uuid.UUID) -> SourceView:
    src = db.get(CashSource, source_id)
    if src is None:
        raise FundingError("fonte inexistente")
    now = _now()
    snap = _latest(db, source_id)
    fresh, configured = _fresh(snap, now), _configured(src)
    committed = _committed(db, source_id, snap.observed_at if snap else None)
    is_reserve = src.kind == "RESERVE"
    return SourceView(
        str(src.id),
        src.code,
        src.kind,
        src.currency,
        configured,
        snap.id if snap else None,
        snap.observed_at.isoformat() if snap else None,
        not fresh,
        snap.available_minor if snap else None,
        snap.retained_minor if snap else None,
        committed,
        src.floor_minor,
        _free(db, src, snap) if (snap and fresh and configured) else None,
        _exposure(db, src.id) if is_reserve else None,
        _period_used(db, src, now) if (is_reserve and src.period_days) else None,
        src.is_simulated,
    )


# ───────────────────────────── Alocação ─────────────────────────────
def _precheck(
    db: Session,
    *,
    currency: str,
    sources: list[CashSource],
    quote_valid_until: dt.datetime,
    order: Order | None,
    supplier: Supplier | None,
    now: dt.datetime,
    allow_during_emergency_stop: bool,
) -> str | None:
    if killswitch.is_active(db) and not allow_during_emergency_stop:
        return "EMERGENCY_STOP"
    if any(s.currency != currency for s in sources):
        return "CURRENCY_MISMATCH"  # BRL × USD sem conversão documentada nunca se soma
    if quote_valid_until <= now:
        return "QUOTE_EXPIRED"
    if order is None or order.payment_status != "CAPTURED" or order.status != "READY_TO_FULFILL":
        return "ORDER_NOT_ELIGIBLE"
    if supplier is None or supplier.status != "APPROVED":
        return "SUPPLIER_NOT_APPROVED"
    return None


def _plan(
    db: Session, op: CashSource, res: CashSource | None, amount: int, now: dt.datetime
) -> tuple[str | None, list[tuple[CashSource, CashBalanceSnapshot, int]]]:
    if not _configured(op):
        return "OPERATING_NOT_CONFIGURED", []
    op_snap = _latest(db, op.id)
    if not _fresh(op_snap, now):
        return "OPERATING_BALANCE_STALE", []
    take = max(0, min(amount, _free(db, op, op_snap)))
    if op.per_order_limit_minor is not None:
        take = min(take, op.per_order_limit_minor)
    legs = [(op, op_snap, take)] if take else []
    rest = amount - take
    if rest == 0:
        return None, legs
    if res is None:
        return "INSUFFICIENT_FUNDS", []
    if not _configured(res):
        return "RESERVE_NOT_CONFIGURED", []
    res_snap = _latest(db, res.id)
    if not _fresh(res_snap, now):
        return "RESERVE_BALANCE_STALE", []
    if rest > _free(db, res, res_snap):
        return "RESERVE_INSUFFICIENT", []
    if rest > res.per_order_limit_minor:
        return "RESERVE_ORDER_LIMIT", []
    if _period_used(db, res, now) + rest > res.period_limit_minor:
        return "RESERVE_PERIOD_LIMIT", []
    if _exposure(db, res.id) + rest > res.max_exposure_minor:
        return "RESERVE_EXPOSURE_LIMIT", []
    return None, [*legs, (res, res_snap, rest)]


def _fingerprint(a: FundingAllocation) -> tuple:
    return (a.amount_minor, a.currency, a.quote_ref, a.beneficiary_ref, a.order_id, a.supplier_id)


def allocate(
    db: Session,
    *,
    obligation_key: str,
    amount_minor: int,
    currency: str,
    quote_ref: str,
    quote_valid_until: dt.datetime,
    beneficiary_ref: str,
    order_id: uuid.UUID,
    supplier_id: uuid.UUID,
    operating_source_id: uuid.UUID,
    actor: str,
    reserve_source_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    allow_during_emergency_stop: bool = False,
) -> tuple[FundingAllocation, bool]:
    """Compromete o total (operação → reserva) ou registra pendência BLOCKED. Retorna (alocação, criada_agora).

    obligation_key identifica a obrigação; replay devolve a alocação viva existente. A exceção ao emergency stop é só
    para pedidos já vendidos (mesma regra do executor e do orçamento), nunca escolha livre do chamador."""
    _require_dry_run()
    if amount_minor <= 0:
        raise FundingError("amount_minor deve ser > 0")
    if reserve_source_id == operating_source_id:
        raise FundingError("fonte de operação e reserva devem ser distintas")
    ids = sorted({operating_source_id, reserve_source_id} - {None}, key=str)
    locked = {
        s.id: s
        for s in db.execute(
            select(CashSource).where(CashSource.id.in_(ids)).order_by(CashSource.id).with_for_update()
        ).scalars()
    }
    op, res = locked.get(operating_source_id), locked.get(reserve_source_id) if reserve_source_id else None
    if op is None or op.kind != "OPERATING":
        raise FundingError("fonte de operação inexistente ou de outro tipo")
    if reserve_source_id and (res is None or res.kind != "RESERVE"):
        raise FundingError("fonte de reserva inexistente ou de outro tipo")
    live = db.execute(
        select(FundingAllocation).where(
            FundingAllocation.obligation_key == obligation_key, FundingAllocation.status.in_(LIVE)
        )
    ).scalar_one_or_none()
    request = (amount_minor, currency, quote_ref, beneficiary_ref, order_id, supplier_id)
    if live:
        if _fingerprint(live) != request:
            raise IdempotencyConflict("obrigação já financiada com dados diferentes")
        return live, False
    now = _now()
    order = db.get(Order, order_id)
    supplier = db.get(Supplier, supplier_id)
    reason = _precheck(
        db,
        currency=currency,
        sources=list(locked.values()),
        quote_valid_until=quote_valid_until,
        order=order,
        supplier=supplier,
        now=now,
        allow_during_emergency_stop=allow_during_emergency_stop,
    )
    legs: list[tuple[CashSource, CashBalanceSnapshot, int]] = []
    if reason is None:
        reason, legs = _plan(db, op, res, amount_minor, now)
    alloc = FundingAllocation(
        obligation_key=obligation_key,
        order_id=order.id if order else None,
        supplier_id=supplier.id if supplier else None,
        proposal_id=proposal_id,
        quote_ref=quote_ref,
        quote_valid_until=quote_valid_until,
        beneficiary_ref=beneficiary_ref,
        amount_minor=amount_minor,
        currency=currency,
        status="BLOCKED" if reason else "RESERVED",
        block_reason=reason,
        payment_attempts=0,
        reserve_recovered_minor=0,
        is_simulated=op.is_simulated,
        created_by=actor,
    )
    db.add(alloc)
    db.flush()
    for src, snap, amt in legs:
        db.add(
            FundingLeg(
                allocation_id=alloc.id,
                source_id=src.id,
                kind=src.kind,
                amount_minor=amt,
                currency=currency,
                snapshot_id=snap.id,
            )
        )
    split = {src.kind: amt for src, _, amt in legs}
    _event(
        db,
        alloc,
        "BLOCKED" if reason else "ALLOCATED",
        f"{alloc.id}:allocated",
        actor=actor,
        amount_minor=amount_minor,
        payload={"reason": reason, "split": split},
    )
    _audit(
        db,
        actor,
        "funding.blocked" if reason else "funding.allocated",
        str(alloc.id),
        {"obligation_key": obligation_key, "amount_minor": amount_minor, "reason": reason, "split": split},
    )
    return alloc, True


def legs(db: Session, allocation_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(select(FundingLeg).where(FundingLeg.allocation_id == allocation_id)).scalars()
    return {leg.kind: leg.amount_minor for leg in rows}


# ───────────────────────────── Pagamento único e reconciliação ─────────────────────────────
def _lock_alloc(db: Session, allocation_id: uuid.UUID) -> FundingAllocation:
    alloc = db.execute(
        select(FundingAllocation).where(FundingAllocation.id == allocation_id).with_for_update()
    ).scalar_one_or_none()
    if alloc is None:
        raise InvalidAllocationState("alocação inexistente")
    return alloc


def start_payment(
    db: Session,
    allocation_id: uuid.UUID,
    *,
    beneficiary_ref: str,
    actor: str,
    allow_during_emergency_stop: bool = False,
) -> dict:
    """Abre a única intenção de pagamento ao fornecedor (valor total, chave estável). Não há segunda tentativa
    enquanto o resultado da anterior não for conhecido."""
    _require_dry_run()
    alloc = _lock_alloc(db, allocation_id)
    if alloc.status in ("PAYMENT_PENDING", "PAYMENT_UNKNOWN"):
        raise PaymentOutcomeUnknown("consulte o provedor e registre o resultado antes de qualquer nova tentativa")
    if alloc.status != "RESERVED":
        raise InvalidAllocationState(f"alocação em {alloc.status}")
    if killswitch.is_active(db) and not allow_during_emergency_stop:
        raise PaymentBlocked("EMERGENCY_STOP")
    if beneficiary_ref != alloc.beneficiary_ref:
        raise PaymentBlocked("BENEFICIARY_CHANGED")
    if alloc.quote_valid_until <= _now():
        raise PaymentBlocked("QUOTE_EXPIRED")
    order = db.get(Order, alloc.order_id) if alloc.order_id else None
    if order is None or order.payment_status != "CAPTURED" or order.status in ("CANCELLED", "REFUNDED"):
        raise PaymentBlocked("ORDER_NOT_ELIGIBLE")
    alloc.status, alloc.payment_attempts = "PAYMENT_PENDING", alloc.payment_attempts + 1
    intent_key = f"pay:{alloc.obligation_key}"
    _event(
        db,
        alloc,
        "PAYMENT_STARTED",
        f"{alloc.id}:start:{alloc.payment_attempts}",
        actor=actor,
        amount_minor=alloc.amount_minor,
        payload={"intent_key": intent_key},
    )
    return {
        "allocation_id": str(alloc.id),
        "intent_key": intent_key,
        "amount_minor": alloc.amount_minor,
        "currency": alloc.currency,
        "beneficiary_ref": alloc.beneficiary_ref,
        "simulated": alloc.is_simulated,
    }


def apply_payment_result(
    db: Session,
    allocation_id: uuid.UUID,
    *,
    outcome: str,
    event_key: str,
    actor: str,
    provider_ref: str | None = None,
) -> tuple[FundingAllocation, bool]:
    """Resultado do provedor (resposta, consulta ou webhook). Duplicados e fora de ordem não mudam o estado.
    Retorna (alocação, aplicado). FAILED só com confirmação de que não houve débito; timeout = UNKNOWN."""
    if outcome not in OUTCOMES:
        raise FundingError(f"resultado inválido: {outcome}")
    if outcome == "SUCCEEDED" and not provider_ref:
        raise FundingError("pagamento confirmado exige referência do provedor")
    alloc = _lock_alloc(db, allocation_id)
    seen = db.execute(select(FundingEvent).where(FundingEvent.idempotency_key == event_key)).scalar_one_or_none()
    if seen:
        if seen.allocation_id != alloc.id or (seen.payload.get("outcome"), seen.external_ref) != (
            outcome,
            provider_ref,
        ):
            raise IdempotencyConflict("evento reutilizado com resultado diferente")
        return alloc, False
    before = alloc.status
    applied = before in ("PAYMENT_PENDING", "PAYMENT_UNKNOWN")
    conflict = False
    if applied:
        if outcome == "SUCCEEDED":
            alloc.status, alloc.payment_ref, alloc.paid_at = "PAID", provider_ref, _now()
        elif outcome == "FAILED":
            alloc.status = "FAILED"
        else:
            alloc.status = "PAYMENT_UNKNOWN"
    elif before == "RESERVED":
        raise InvalidAllocationState("resultado de pagamento sem intenção aberta")
    else:
        # Fora de ordem: PAID não volta atrás. Sucesso depois de FAILED/RELEASED é débito não previsto → humano.
        conflict = (
            (before == "PAID" and outcome == "FAILED")
            or (before in ("FAILED", "RELEASED") and outcome == "SUCCEEDED")
            or (before == "PAID" and outcome == "SUCCEEDED" and provider_ref != alloc.payment_ref)
        )
    _event(
        db,
        alloc,
        f"PAYMENT_{outcome}" if applied else ("RECONCILIATION_CONFLICT" if conflict else "PAYMENT_RESULT_IGNORED"),
        event_key,
        actor=actor,
        amount_minor=alloc.amount_minor,
        external_ref=provider_ref,
        payload={"outcome": outcome, "before": before, "after": alloc.status},
    )
    if applied or conflict:
        _audit(
            db,
            actor,
            "funding.reconciliation_conflict" if conflict else f"funding.payment.{outcome.lower()}",
            str(alloc.id),
            {"before": before, "after": alloc.status, "provider_ref": provider_ref},
        )
    return alloc, applied


def release(db: Session, allocation_id: uuid.UUID, *, reason: str, actor: str) -> FundingAllocation:
    """Cancelamento antes do pagamento devolve o dinheiro comprometido. Com pagamento em curso ou feito, não."""
    alloc = _lock_alloc(db, allocation_id)
    if alloc.status in ("PAYMENT_PENDING", "PAYMENT_UNKNOWN"):
        raise PaymentOutcomeUnknown("pagamento em curso/incerto: reconcilie antes de liberar")
    if alloc.status == "PAID":
        raise InvalidAllocationState("já paga: estorno do fornecedor é outro fluxo; exposição continua registrada")
    if alloc.status != "RESERVED":
        raise InvalidAllocationState(f"alocação em {alloc.status}")
    alloc.status = "RELEASED"
    _event(db, alloc, "RELEASED", f"{alloc.id}:released", actor=actor, payload={"reason": reason})
    _audit(db, actor, "funding.released", str(alloc.id), {"reason": reason})
    return alloc


def recognize_receivable(
    db: Session,
    allocation_id: uuid.UUID,
    *,
    amount_minor: int,
    currency: str,
    external_ref: str,
    idempotency_key: str,
    actor: str,
) -> dict:
    """Recebível do pedido ficou disponível: abate a exposição da reserva desta alocação. Só contábil —
    não paga o fornecedor de novo e não presume transferência de recomposição para o fundo."""
    if amount_minor <= 0:
        raise FundingError("amount_minor deve ser > 0")
    alloc = _lock_alloc(db, allocation_id)
    seen = db.execute(select(FundingEvent).where(FundingEvent.idempotency_key == idempotency_key)).scalar_one_or_none()
    if seen:
        if (seen.allocation_id, seen.payload.get("received_minor"), seen.external_ref) != (
            alloc.id,
            amount_minor,
            external_ref,
        ):
            raise IdempotencyConflict("recebível reutilizado com dados diferentes")
        return {"recovered_minor": 0, "replayed": True, "exposure_minor": _alloc_exposure(db, alloc)}
    if alloc.status != "PAID":
        raise InvalidAllocationState(f"recebível só abate exposição de alocação paga (está {alloc.status})")
    if currency != alloc.currency:
        raise FundingError("moeda do recebível difere da obrigação")
    recovered = min(amount_minor, _alloc_exposure(db, alloc))
    alloc.reserve_recovered_minor += recovered
    _event(
        db,
        alloc,
        "RESERVE_RECOVERED",
        idempotency_key,
        actor=actor,
        amount_minor=recovered,
        external_ref=external_ref,
        payload={"received_minor": amount_minor, "recovered_minor": recovered},
    )
    _audit(db, actor, "funding.reserve.recovered", str(alloc.id), {"recovered_minor": recovered, "ref": external_ref})
    return {"recovered_minor": recovered, "replayed": False, "exposure_minor": _alloc_exposure(db, alloc)}


def _alloc_exposure(db: Session, alloc: FundingAllocation) -> int:
    return legs(db, alloc.id).get("RESERVE", 0) - alloc.reserve_recovered_minor


def pay_simulated(db: Session, allocation_id: uuid.UUID, *, actor: str) -> FundingAllocation:
    """DRY_RUN: intenção + confirmação simulada. Nenhum dinheiro sai de lugar algum."""
    _require_dry_run()
    alloc = db.get(FundingAllocation, allocation_id)
    if alloc is None or not alloc.is_simulated:
        raise FundingDisabled("pagamento simulado só em alocação simulada")
    start_payment(db, allocation_id, beneficiary_ref=alloc.beneficiary_ref, actor=actor)
    out, _ = apply_payment_result(
        db,
        allocation_id,
        outcome="SUCCEEDED",
        event_key=f"{allocation_id}:sim",
        provider_ref=f"SIM-PAY-{uuid.UUID(str(allocation_id)).hex[:12]}",
        actor=actor,
    )
    return out


def pending_reconciliation(db: Session, *, older_than_s: int = 0) -> list[FundingAllocation]:
    """Pagamentos com resultado desconhecido (crash, timeout): exigem consulta ao provedor, nunca novo débito."""
    cutoff = _now() - dt.timedelta(seconds=older_than_s)
    return list(
        db.execute(
            select(FundingAllocation)
            .where(
                FundingAllocation.status.in_(("PAYMENT_PENDING", "PAYMENT_UNKNOWN")),
                FundingAllocation.updated_at <= cutoff,
            )
            .order_by(FundingAllocation.updated_at)
        ).scalars()
    )


# ───────────────────────────── Integração com o executor (SUPPLIER_PURCHASE) ─────────────────────────────
def enabled() -> bool:
    """Ativo só quando a fonte de operação está configurada; sem isso o executor segue apenas com o orçamento."""
    return bool(get_settings().funding_operating_source)


def _source_by_code(db: Session, code: str) -> CashSource | None:
    return db.execute(select(CashSource).where(CashSource.code == code)).scalar_one_or_none()


def open_for_proposal(db: Session, prop: ApprovalProposal, *, deadline: dt.datetime) -> str | None:
    """Executor, antes da chamada ao fornecedor (mesma transação que marca EXECUTING): cobre o total com caixa e
    abre a intenção única. Retorna None se coberto ou o motivo do bloqueio (a pendência BLOCKED fica registrada).

    Obrigação = pedido + fornecedor: uma segunda compra do mesmo par, com a primeira viva ou paga, é recusada."""
    s = get_settings()
    op = _source_by_code(db, s.funding_operating_source)
    res = _source_by_code(db, s.funding_reserve_source) if s.funding_reserve_source else None
    if op is None or (s.funding_reserve_source and res is None):
        return "fonte de caixa configurada não existe"
    f = db.execute(select(Fulfillment).where(Fulfillment.proposal_id == prop.id)).scalar_one_or_none()
    if f is None:
        return "compra sem envio solicitado"
    offers = [
        db.get(SupplierOffer, it.offer_id)
        for it in db.execute(select(OrderItem).where(OrderItem.order_id == f.order_id)).scalars()
        if it.offer_id
    ]
    offers = [o for o in offers if o is not None and o.supplier_id == f.supplier_id]
    if not offers or any(o.status != "ACTIVE" for o in offers):
        return "oferta do fornecedor ausente ou inativa"
    currencies = {o.currency for o in offers}
    if len(currencies) != 1:
        return "ofertas do mesmo fornecedor em moedas diferentes"
    supplier = db.get(Supplier, f.supplier_id)
    beneficiary = f"supplier:{supplier.id}:v{supplier.version}"  # recebedor real (PIX/carteira) ainda não modelado
    try:
        alloc, _ = allocate(
            db,
            obligation_key=f"supplier-purchase:{f.order_id}:{f.supplier_id}",
            amount_minor=f.cost_minor,
            currency=currencies.pop(),
            quote_ref=f"{prop.id}:{prop.payload_sha256[:16]}",
            quote_valid_until=min([o.valid_until for o in offers if o.valid_until] + [deadline]),
            beneficiary_ref=beneficiary,
            order_id=f.order_id,
            supplier_id=f.supplier_id,
            operating_source_id=op.id,
            reserve_source_id=res.id if res else None,
            proposal_id=prop.id,
            actor="executor",
            allow_during_emergency_stop=True,
        )
    except IdempotencyConflict:
        return "obrigação deste pedido com este fornecedor já financiada por outra proposta"
    except FundingDisabled as e:
        return str(e)
    if alloc.status == "BLOCKED":
        return f"caixa insuficiente ou bloqueado: {alloc.block_reason}"
    if alloc.status != "RESERVED":
        return f"obrigação já em {alloc.status}"
    try:
        start_payment(db, alloc.id, beneficiary_ref=beneficiary, actor="executor", allow_during_emergency_stop=True)
    except PaymentBlocked as e:
        release(db, alloc.id, reason=f"pagamento bloqueado: {e.reason}", actor="executor")
        return f"pagamento bloqueado: {e.reason}"
    return None


def settle_for_proposal(
    db: Session, prop: ApprovalProposal, *, outcome: str, event_key: str, provider_ref: str | None = None
) -> None:
    """Executor/reconciliador: aplica o resultado do fornecedor à intenção aberta desta proposta, se houver."""
    alloc = db.execute(
        select(FundingAllocation).where(
            FundingAllocation.proposal_id == prop.id,
            FundingAllocation.status.in_(("PAYMENT_PENDING", "PAYMENT_UNKNOWN")),
        )
    ).scalar_one_or_none()
    if alloc is None:
        return
    apply_payment_result(
        db,
        alloc.id,
        outcome=outcome,
        event_key=event_key,
        provider_ref=provider_ref or (f"proposal:{prop.id}" if outcome == "SUCCEEDED" else None),
        actor="executor",
    )
