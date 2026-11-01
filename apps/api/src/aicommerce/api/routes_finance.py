from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.api.deps import require
from aicommerce.db import get_db
from aicommerce.finance import ledger
from aicommerce.identity.security import Principal
from aicommerce.models import BudgetReservation, MoneyAccount

router = APIRouter(prefix="/v1/finance", tags=["finance"])

ERROR_STATUS = {
    ledger.InsufficientBudget: status.HTTP_409_CONFLICT,
    ledger.IdempotencyConflict: status.HTTP_409_CONFLICT,
    ledger.SpendingFrozen: status.HTTP_423_LOCKED,
    ledger.InvalidReservationState: status.HTTP_409_CONFLICT,
}


def _raise(e: ledger.LedgerError):
    raise HTTPException(
        ERROR_STATUS.get(type(e), status.HTTP_422_UNPROCESSABLE_CONTENT), {"code": e.code, "message": str(e)}
    )


def resv_out(r: BudgetReservation) -> dict:
    return {
        "id": str(r.id),
        "account_id": str(r.account_id),
        "amount_minor": r.amount_minor,
        "consumed_minor": r.consumed_minor,
        "currency": r.currency,
        "status": r.status,
        "purpose": r.purpose,
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: Principal = Depends(require("finance:read"))):
    accounts = db.execute(select(MoneyAccount).order_by(MoneyAccount.code)).scalars().all()
    items = []
    for a in accounts:
        try:
            items.append({"code": a.code, "name": a.name, **ledger.budget_view(db, a.id).as_dict()})
        except ledger.LedgerError:
            items.append(
                {"code": a.code, "name": a.name, "account_id": str(a.id), "budget": None, "simulated": a.is_simulated}
            )
    return {"accounts": items}


class ReserveIn(BaseModel):
    account_id: uuid.UUID
    amount_minor: int = Field(gt=0)
    currency: str = Field(default="BRL", pattern=r"^[A-Z]{3}$")
    purpose: str = Field(min_length=3, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=128)
    proposal_id: uuid.UUID | None = None


@router.post("/reservations", status_code=status.HTTP_201_CREATED)
def reserve(body: ReserveIn, db: Session = Depends(get_db), p: Principal = Depends(require("finance:reserve"))):
    try:
        r, created = ledger.reserve(
            db,
            body.account_id,
            amount_minor=body.amount_minor,
            currency=body.currency,
            idempotency_key=body.idempotency_key,
            purpose=body.purpose,
            actor=str(p.user_id),
            proposal_id=body.proposal_id,
        )
    except ledger.LedgerError as e:
        _raise(e)
    return {**resv_out(r), "replayed": not created}


class SpendIn(BaseModel):
    account_id: uuid.UUID
    amount_minor: int = Field(gt=0)
    currency: str = Field(default="BRL", pattern=r"^[A-Z]{3}$")
    external_ref: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)
    reservation_id: uuid.UUID | None = None


@router.post("/spend")
def spend(body: SpendIn, db: Session = Depends(get_db), p: Principal = Depends(require("finance:reconcile"))):
    try:
        return ledger.record_spend(
            db,
            body.account_id,
            amount_minor=body.amount_minor,
            currency=body.currency,
            external_ref=body.external_ref,
            idempotency_key=body.idempotency_key,
            actor=str(p.user_id),
            reservation_id=body.reservation_id,
        )
    except ledger.LedgerError as e:
        _raise(e)


class ReleaseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/reservations/{reservation_id}/release")
def release(
    reservation_id: uuid.UUID,
    body: ReleaseIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("finance:reconcile")),
):
    try:
        return resv_out(ledger.release(db, reservation_id, actor=str(p.user_id), reason=body.reason))
    except ledger.LedgerError as e:
        _raise(e)


class BudgetIn(BaseModel):
    approved_cap_minor: int = Field(ge=0)
    safety_hold_minor: int = Field(ge=0)


@router.put("/accounts/{account_id}/budget")
def set_budget(
    account_id: uuid.UUID,
    body: BudgetIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("finance:manage")),
):
    try:
        ledger.set_budget(
            db,
            account_id,
            approved_cap_minor=body.approved_cap_minor,
            safety_hold_minor=body.safety_hold_minor,
            approved_by=str(p.user_id),
        )
        return ledger.budget_view(db, account_id).as_dict()
    except ledger.LedgerError as e:
        _raise(e)
