"""Inbox de aprovações (SDD §14): detalhe, aprovar, rejeitar, pedir mudanças, revogar."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aicommerce.api.deps import request_id, require
from aicommerce.api.routes_governance import proposal_out
from aicommerce.config import get_settings
from aicommerce.db import get_db, session_scope
from aicommerce.governance import approvals
from aicommerce.identity.security import Principal
from aicommerce.models import ApprovalProposal
from aicommerce.providers import registry

router = APIRouter(prefix="/v1", tags=["approvals"])


class DecisionIn(BaseModel):
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="hash do payload exibido ao decisor")
    subject_version: int = Field(ge=1)
    comment: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class RevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


def _http(e: approvals.ApprovalError) -> HTTPException:
    return HTTPException(e.http, {"code": e.code, "message": str(e)})


def _full(db: Session, prop: ApprovalProposal) -> dict[str, Any]:
    return {**proposal_out(prop), **approvals.detail(db, prop), "summary": approvals.summary(db, prop)}


def _expire_first(proposal_id: uuid.UUID) -> None:
    # Transação própria: a expiração persiste mesmo que a decisão seguinte seja recusada.
    with session_scope() as db:
        prop = db.get(ApprovalProposal, proposal_id, with_for_update=True)
        if prop is not None:
            approvals.expire_if_due(db, prop)


@router.get("/approvals/{proposal_id}")
def get_approval(
    proposal_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("approvals:read"))
):
    prop = db.get(ApprovalProposal, proposal_id)
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proposta inexistente")
    return _full(db, prop)


def _decide(decision: str):
    def endpoint(
        proposal_id: uuid.UUID,
        body: DecisionIn,
        db: Session = Depends(get_db),
        p: Principal = Depends(require("approvals:decide")),
        rid: str = Depends(request_id),
    ):
        try:
            prop, created = approvals.decide(
                db,
                proposal_id,
                decision=decision,
                principal=p,
                payload_sha256=body.payload_sha256,
                subject_version=body.subject_version,
                comment=body.comment,
                idempotency_key=body.idempotency_key,
                request_id=rid,
            )
        except approvals.ApprovalError as e:
            raise _http(e) from e
        return {**_full(db, prop), "replayed": not created}

    return endpoint


for _path, _decision in (("approve", "APPROVE"), ("reject", "REJECT"), ("request-changes", "REQUEST_CHANGES")):
    router.add_api_route(
        f"/approvals/{{proposal_id}}/{_path}",
        _decide(_decision),
        methods=["POST"],
        dependencies=[Depends(_expire_first)],
        name=f"approval_{_path}",
    )


@router.post("/approvals/{proposal_id}/revoke")
def revoke(
    proposal_id: uuid.UUID,
    body: RevokeIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("approvals:decide")),
    rid: str = Depends(request_id),
):
    try:
        prop, created = approvals.revoke(
            db, proposal_id, principal=p, reason=body.reason, idempotency_key=body.idempotency_key, request_id=rid
        )
    except approvals.ApprovalError as e:
        raise _http(e) from e
    return {**_full(db, prop), "replayed": not created}


@router.get("/channels/{channel}/capabilities")
def capabilities(channel: str, _: Principal = Depends(require("office:read"))):
    channel = channel.upper()
    if channel not in registry.CHANNELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "canal desconhecido")
    mode = get_settings().mode.value
    return {
        "channel": channel,
        "mode": mode,
        "simulated": mode == "DRY_RUN",
        "capabilities": registry.channel_capabilities(channel, mode),
    }
