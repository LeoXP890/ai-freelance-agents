"""Ledger imutável + reservas transacionais (SDD §8, FR-008/GOV-003).

available = approved_cap − reconciled_spend − outstanding_reservations − safety_hold

* Toda reserva bloqueia a linha de budget_policies (SELECT … FOR UPDATE): reservas concorrentes são
  serializadas e nunca autorizadas por saldo lido fora da transação.
* Gasto reconciliado consome a reserva correspondente (sem dupla subtração).
* Gasto externo acima da reserva é registrado assim mesmo (a plataforma já gastou) e sinalizado.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import audit, killswitch
from aicommerce.models import BudgetPolicy, BudgetReservation, LedgerEntry, MoneyAccount


class LedgerError(Exception):
    code = "LEDGER_ERROR"


class InsufficientBudget(LedgerError):
    code = "INSUFFICIENT_BUDGET"


class IdempotencyConflict(LedgerError):
    code = "IDEMPOTENCY_CONFLICT"


class SpendingFrozen(LedgerError):
    code = "EMERGENCY_STOP_ACTIVE"


class InvalidReservationState(LedgerError):
    code = "INVALID_RESERVATION_STATE"


@dataclass(frozen=True)
class BudgetView:
    account_id: str
    currency: str
    approved_cap_minor: int
    reconciled_spend_minor: int
    outstanding_reservations_minor: int
    safety_hold_minor: int
    available_minor: int
    simulated: bool

    def as_dict(self) -> dict:
        return asdict(self)


def ensure_account(db: Session, code: str, name: str, currency: str = "BRL") -> MoneyAccount:
    acc = db.execute(select(MoneyAccount).where(MoneyAccount.code == code)).scalar_one_or_none()
    if acc:
        return acc
    acc = MoneyAccount(code=code, name=name, currency=currency, is_simulated=get_settings().is_dry_run)
    db.add(acc)
    db.flush()
    return acc


def set_budget(
    db: Session, account_id: uuid.UUID, *, approved_cap_minor: int, safety_hold_minor: int, approved_by: str
) -> BudgetPolicy:
    if approved_cap_minor < 0 or safety_hold_minor < 0:
        raise LedgerError("valores não podem ser negativos")
    acc = db.get(MoneyAccount, account_id)
    if acc is None:
        raise LedgerError("conta inexistente")
    pol = db.get(BudgetPolicy, account_id, with_for_update=True)
    before = None
    if pol is None:
        pol = BudgetPolicy(
            account_id=account_id,
            approved_cap_minor=approved_cap_minor,
            safety_hold_minor=safety_hold_minor,
            currency=acc.currency,
            approved_by=approved_by,
        )
        db.add(pol)
    else:
        before = {"cap": pol.approved_cap_minor, "hold": pol.safety_hold_minor, "version": pol.version}
        pol.approved_cap_minor, pol.safety_hold_minor = approved_cap_minor, safety_hold_minor
        pol.approved_by, pol.version, pol.updated_at = approved_by, pol.version + 1, dt.datetime.now(dt.UTC)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=approved_by,
        action="budget.policy.set",
        subject_type="money_account",
        subject_id=str(account_id),
        payload={"before": before, "cap": approved_cap_minor, "hold": safety_hold_minor},
    )
    return pol


def _view(db: Session, pol: BudgetPolicy, acc: MoneyAccount) -> BudgetView:
    spend = db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            LedgerEntry.account_id == pol.account_id, LedgerEntry.entry_type == "SPEND"
        )
    ).scalar_one()
    outstanding = db.execute(
        select(func.coalesce(func.sum(BudgetReservation.amount_minor - BudgetReservation.consumed_minor), 0)).where(
            BudgetReservation.account_id == pol.account_id, BudgetReservation.status == "ACTIVE"
        )
    ).scalar_one()
    available = pol.approved_cap_minor - int(spend) - int(outstanding) - pol.safety_hold_minor
    return BudgetView(
        str(pol.account_id),
        pol.currency,
        pol.approved_cap_minor,
        int(spend),
        int(outstanding),
        pol.safety_hold_minor,
        available,
        acc.is_simulated,
    )


def budget_view(db: Session, account_id: uuid.UUID) -> BudgetView:
    pol = db.get(BudgetPolicy, account_id)
    acc = db.get(MoneyAccount, account_id)
    if pol is None or acc is None:
        raise LedgerError("conta sem política de orçamento aprovada")
    return _view(db, pol, acc)


def _lock(db: Session, account_id: uuid.UUID) -> tuple[BudgetPolicy, MoneyAccount]:
    pol = db.execute(
        select(BudgetPolicy).where(BudgetPolicy.account_id == account_id).with_for_update()
    ).scalar_one_or_none()
    acc = db.get(MoneyAccount, account_id)
    if pol is None or acc is None:
        raise LedgerError("conta sem política de orçamento aprovada")
    return pol, acc


def reserve(
    db: Session,
    account_id: uuid.UUID,
    *,
    amount_minor: int,
    currency: str,
    idempotency_key: str,
    purpose: str,
    actor: str,
    proposal_id: uuid.UUID | None = None,
    expires_at: dt.datetime | None = None,
    allow_during_emergency_stop: bool = False,
) -> tuple[BudgetReservation, bool]:
    """Retorna (reserva, criada_agora). Replay com mesma chave e mesmos dados devolve a original."""
    if amount_minor <= 0:
        raise LedgerError("amount_minor deve ser > 0")
    pol, acc = _lock(db, account_id)
    existing = db.execute(
        select(BudgetReservation).where(BudgetReservation.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        same = (existing.account_id, existing.amount_minor, existing.currency) == (account_id, amount_minor, currency)
        if not same:
            raise IdempotencyConflict("idempotency_key reutilizada com payload diferente")
        return existing, False
    if killswitch.is_active(db) and not allow_during_emergency_stop:
        # Exceção só para obrigações de pedidos já contratados (decidida pela política, não pelo chamador livre).
        raise SpendingFrozen("emergency stop ativo: novas reservas bloqueadas")
    if currency != pol.currency:
        raise LedgerError(f"moeda {currency} difere da conta ({pol.currency})")
    view = _view(db, pol, acc)
    if amount_minor > view.available_minor:
        raise InsufficientBudget(f"disponível {view.available_minor} < solicitado {amount_minor}")
    resv = BudgetReservation(
        account_id=account_id,
        amount_minor=amount_minor,
        currency=currency,
        status="ACTIVE",
        purpose=purpose,
        proposal_id=proposal_id,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
    )
    db.add(resv)
    db.flush()
    db.add(
        LedgerEntry(
            account_id=account_id,
            entry_type="RESERVE",
            amount_minor=amount_minor,
            currency=currency,
            reservation_id=resv.id,
            idempotency_key=f"reserve:{idempotency_key}",
            memo=purpose,
            created_by=actor,
        )
    )
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="budget.reserved",
        subject_type="budget_reservation",
        subject_id=str(resv.id),
        payload={"amount_minor": amount_minor, "currency": currency, "available_before": view.available_minor},
    )
    from aicommerce.office import events

    events.emit(
        db,
        "budget.reserved",
        payload={"reservation_id": str(resv.id), "amount_minor": amount_minor, "currency": currency},
    )
    return resv, True


def record_spend(
    db: Session,
    account_id: uuid.UUID,
    *,
    amount_minor: int,
    currency: str,
    external_ref: str,
    idempotency_key: str,
    actor: str,
    reservation_id: uuid.UUID | None = None,
) -> dict:
    """Assimila gasto externo reconciliado. Permitido mesmo com kill switch (o gasto já ocorreu)."""
    if amount_minor <= 0:
        raise LedgerError("amount_minor deve ser > 0")
    pol, acc = _lock(db, account_id)
    dup = db.execute(select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)).scalar_one_or_none()
    if dup:
        if (dup.amount_minor, dup.entry_type, dup.external_ref) != (amount_minor, "SPEND", external_ref):
            raise IdempotencyConflict("idempotency_key reutilizada com payload diferente")
        return {"entry_id": dup.id, "replayed": True, "budget": _view(db, pol, acc).as_dict(), "overspend": False}
    if currency != pol.currency:
        raise LedgerError(f"moeda {currency} difere da conta ({pol.currency})")
    consumed_now = 0
    if reservation_id:
        resv = db.get(BudgetReservation, reservation_id, with_for_update=True)
        if resv is None or resv.account_id != account_id:
            raise InvalidReservationState("reserva inexistente nesta conta")
        if resv.status != "ACTIVE":
            raise InvalidReservationState(f"reserva em estado {resv.status}")
        consumed_now = min(amount_minor, resv.amount_minor - resv.consumed_minor)
        resv.consumed_minor += consumed_now
        if resv.consumed_minor == resv.amount_minor:
            resv.status = "CONSUMED"
    entry = LedgerEntry(
        account_id=account_id,
        entry_type="SPEND",
        amount_minor=amount_minor,
        currency=currency,
        reservation_id=reservation_id,
        external_ref=external_ref,
        idempotency_key=idempotency_key,
        created_by=actor,
        memo=f"consumiu {consumed_now} da reserva",
    )
    db.add(entry)
    db.flush()
    view = _view(db, pol, acc)
    overspend = view.available_minor < 0
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="budget.spend.reconciled",
        subject_type="ledger_entry",
        subject_id=str(entry.id),
        payload={
            "amount_minor": amount_minor,
            "external_ref": external_ref,
            "consumed_from_reservation": consumed_now,
            "overspend": overspend,
        },
    )
    return {"entry_id": entry.id, "replayed": False, "budget": view.as_dict(), "overspend": overspend}


def release(db: Session, reservation_id: uuid.UUID, *, actor: str, reason: str) -> BudgetReservation:
    resv = db.get(BudgetReservation, reservation_id)
    if resv is None:
        raise InvalidReservationState("reserva inexistente")
    _lock(db, resv.account_id)
    db.refresh(resv, with_for_update=True)
    if resv.status != "ACTIVE":
        raise InvalidReservationState(f"reserva em estado {resv.status}")
    remaining = resv.amount_minor - resv.consumed_minor
    resv.status = "RELEASED"
    db.add(
        LedgerEntry(
            account_id=resv.account_id,
            entry_type="RELEASE",
            amount_minor=remaining,
            currency=resv.currency,
            reservation_id=resv.id,
            idempotency_key=f"release:{resv.id}",
            memo=reason,
            created_by=actor,
        )
    )
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="budget.reservation.released",
        subject_type="budget_reservation",
        subject_id=str(resv.id),
        payload={"released_minor": remaining, "reason": reason},
    )
    db.flush()
    return resv
