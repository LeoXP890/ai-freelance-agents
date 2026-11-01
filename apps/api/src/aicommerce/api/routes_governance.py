from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.api.deps import current_principal, request_id, require
from aicommerce.db import get_db
from aicommerce.governance import audit, golive, killswitch, readiness, service
from aicommerce.identity.security import Principal
from aicommerce.models import ApprovalProposal, CertificationGate

router = APIRouter(prefix="/v1", tags=["governance"])


class GoLiveIn(BaseModel):
    channel: golive.Channel
    supplier_origin: golive.SupplierOrigin
    fulfillment_mode: golive.FulfillmentMode
    uf: str = Field(pattern=r"^[A-Za-z]{2}$")
    product_id: str | None = None
    supplier_id: str | None = None

    def to_req(self) -> golive.GoLiveRequest:
        return golive.GoLiveRequest(
            self.channel,
            self.supplier_origin,
            self.fulfillment_mode,
            self.uf.upper(),
            self.product_id,
            self.supplier_id,
        )


class ProposalIn(BaseModel):
    action_type: str = Field(max_length=64)
    agent_id: str | None = None
    subject_type: str = Field(max_length=64)
    subject_id: str = Field(max_length=128)
    subject_version: int = Field(ge=1)
    payload: dict[str, Any]
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    monetary_scope_minor: int = 0
    currency: str = Field(default="BRL", pattern=r"^[A-Z]{3}$")
    channel_account_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)
    budget_account_id: uuid.UUID | None = Field(default=None, description="obrigatória para ações monetárias")
    go_live: GoLiveIn | None = Field(default=None, description="contexto de canal para ações com efeito externo")


def proposal_out(p: ApprovalProposal) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "action_type": p.action_type,
        "agent_id": p.agent_id,
        "subject_type": p.subject_type,
        "subject_id": p.subject_id,
        "subject_version": p.subject_version,
        "payload_sha256": p.payload_sha256,
        "risk_score": p.risk_score,
        "evidence_refs": p.evidence_refs,
        "monetary_scope_minor": p.monetary_scope_minor,
        "currency": p.currency,
        "mode": p.mode,
        "simulated": p.mode == "DRY_RUN",
        "policy_version": p.policy_version,
        "policy_decision": p.policy_decision,
        "policy_reasons": p.policy_reasons,
        "status": p.status,
        "expires_at": p.expires_at.isoformat(),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.post("/proposals", status_code=status.HTTP_201_CREATED)
def submit(
    body: ProposalIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("proposals:create")),
    rid: str = Depends(request_id),
):
    try:
        prop, created = service.submit_proposal(
            db,
            body.model_dump(exclude={"go_live"}),
            actor=str(p.user_id),
            request_id=rid,
            golive_req=body.go_live.to_req() if body.go_live else None,
        )
    except service.ProposalConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return {**proposal_out(prop), "replayed": not created}


@router.get("/approvals")
def list_approvals(
    status_: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("approvals:read")),
):
    q = select(ApprovalProposal).order_by(ApprovalProposal.created_at.desc()).limit(200)
    if status_:
        q = q.where(ApprovalProposal.status == status_)
    from aicommerce.governance import approvals as _approvals

    return {"items": [{**proposal_out(x), "summary": _approvals.summary(db, x)} for x in db.execute(q).scalars()]}


@router.post("/go-live/evaluate")
def evaluate_golive(
    body: GoLiveIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("golive:evaluate")),
    rid: str = Depends(request_id),
):
    res = service.evaluate_golive(db, body.to_req(), actor=str(p.user_id), request_id=rid)
    return {
        "decision": res.decision,
        "required_gates": res.required_gates,
        "unmet": res.unmet,
        "restrictions": res.restrictions,
        "seller_type": res.seller_type,
        "summary": res.summary,
        "evaluated_at": dt.datetime.now(dt.UTC).isoformat(),
    }


@router.get("/go-live/readiness")
def get_golive_readiness(
    uf: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
    supplier_origin: golive.SupplierOrigin | None = None,
    fulfillment_mode: golive.FulfillmentMode | None = None,
    product_id: str | None = Query(default=None, max_length=128),
    supplier_id: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("golive:evaluate")),
):
    """Visão somente leitura para agentes/operador: nenhum gate é aprovado automaticamente."""
    return readiness.report(
        db,
        uf=uf,
        supplier_origin=supplier_origin,
        fulfillment_mode=fulfillment_mode,
        product_id=product_id,
        supplier_id=supplier_id,
    )


@router.get("/gates")
def list_gates(db: Session = Depends(get_db), _: Principal = Depends(require("golive:evaluate"))):
    from aicommerce.governance import seller

    st = seller.get_profile(db).person_type
    gates = db.execute(select(CertificationGate).order_by(CertificationGate.code)).scalars()
    return {
        "seller_type": st,
        "items": [
            {
                "code": g.code,
                "state": g.state,
                **seller.gate_text(g.code, _texts(g), st),
                "valid_until": g.valid_until.isoformat() if g.valid_until else None,
                "approved_for": g.scope.get("seller_types")
                if g.state in ("APPROVED", "APPROVED_WITH_RESTRICTIONS")
                else None,
            }
            for g in gates
        ],
    }


def _texts(g: CertificationGate) -> dict[str, str]:
    return {
        "title": g.title,
        "expected_evidence": g.expected_evidence,
        "responsible": g.responsible,
        "blocks": g.blocks,
    }


class StopIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/emergency-stop")
def stop_state(db: Session = Depends(get_db), _: Principal = Depends(require("office:read"))):
    return killswitch.state(db)


@router.post("/emergency-stop")
def stop(
    body: StopIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("emergency:stop")),
    rid: str = Depends(request_id),
):
    current = killswitch.state(db, for_update=True)
    if current.get("active"):
        return {**current, "replayed": True}
    return killswitch.activate(db, actor=str(p.user_id), reason=body.reason, request_id=rid)


@router.get("/emergency-stop/report")
def stop_report(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    """Relatório pós-stop (GOV-005): pausas pedidas × confirmadas, filas congeladas, gasto no período."""
    if not (p.has("emergency:stop") or p.has("audit:read")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "permissão necessária: emergency:stop ou audit:read")
    return killswitch.report(db)


@router.post("/emergency-stop/release")
def release_stop(
    body: StopIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("emergency:stop")),
    rid: str = Depends(request_id),
):
    if "owner" not in p.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "liberação do kill switch exige papel owner")
    current = killswitch.state(db, for_update=True)
    if not current.get("active"):
        return {**current, "replayed": True}
    return killswitch.release(db, actor=str(p.user_id), note=body.reason, request_id=rid)


@router.get("/audit/verify")
def verify_audit(db: Session = Depends(get_db), _: Principal = Depends(require("audit:read"))):
    ok, bad = audit.verify_chain(db)
    return {"intact": ok, "first_invalid_id": bad}


@router.get("/audit/events")
def audit_events(
    subject_id: str | None = None,
    action: str | None = Query(default=None, max_length=64, description="prefixo, ex.: 'approval.'"),
    actor_id: str | None = Query(default=None, max_length=128),
    before_id: int | None = Query(default=None, ge=1, description="paginação: eventos com id menor"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("audit:read")),
):
    from aicommerce.models import AuditEvent, User

    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if subject_id:
        q = q.where(AuditEvent.subject_id == subject_id)
    if action:  # um ou mais prefixos separados por vírgula (ex.: "agent_task.,mission.")
        from sqlalchemy import or_

        prefixes = [a.strip() for a in action.split(",") if a.strip()][:10]
        q = q.where(or_(*(AuditEvent.action.startswith(a, autoescape=True) for a in prefixes)))
    if actor_id:
        q = q.where(AuditEvent.actor_id == actor_id)
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    rows = list(db.execute(q).scalars())
    ids = set()
    for e in rows:
        try:
            ids.add(uuid.UUID(e.actor_id))
        except (ValueError, TypeError):
            pass
    emails = (
        {str(k): v for k, v in db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()} if ids else {}
    )
    return {
        "items": [
            {
                "id": e.id,
                "occurred_at": e.occurred_at.isoformat(),
                "actor_type": e.actor_type,
                "actor_id": e.actor_id,
                "actor_label": emails.get(e.actor_id) or e.actor_id,
                "action": e.action,
                "subject_type": e.subject_type,
                "subject_id": e.subject_id,
                "mode": e.mode,
                "payload": e.payload,
            }
            for e in rows
        ],
        "next_before_id": rows[-1].id if len(rows) == limit else None,
    }


# ───────────── Ciclo de vida dos gates (GOV-006) ─────────────
class EvidenceIn(BaseModel):
    kind: str = Field(max_length=32)
    reference: str = Field(min_length=3, max_length=1000, description="onde o documento/parecer está guardado")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: str = Field(default="", max_length=2000)


class GateDecisionIn(BaseModel):
    decision: Literal["APPROVED", "APPROVED_WITH_RESTRICTIONS", "REJECTED"]
    scope: dict[str, Any] = Field(default_factory=dict)
    valid_until: dt.datetime | None = None
    note: str = Field(min_length=10, max_length=2000)


class GateRevokeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


def _gate_detail(db: Session, code: str) -> dict[str, Any]:
    from aicommerce.governance import gates
    from aicommerce.models import GateEvidence

    g = db.get(CertificationGate, code)
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "gate inexistente")
    ev = db.execute(
        select(GateEvidence).where(GateEvidence.gate_code == code).order_by(GateEvidence.submitted_at)
    ).scalars()
    from aicommerce.governance import seller

    st = seller.get_profile(db).person_type
    kinds = {e.kind for e in db.execute(select(GateEvidence).where(GateEvidence.gate_code == code)).scalars()}
    approved = g.state in ("APPROVED", "APPROVED_WITH_RESTRICTIONS")
    sensitive = code in seller.PROFILE_SENSITIVE_GATES
    requirements = [
        {"label": "Evidências registradas", "met": bool(kinds)},
        *(
            [
                {
                    "label": "Parecer de profissional habilitado ou aceite de risco do proprietário anexado",
                    "met": bool({"PARECER", "ACEITE_RISCO"} & kinds),
                }
            ]
            if st == "PERSON" and code in seller.PERSON_OPINION_GATES
            else []
        ),
        {"label": "Aprovação humana com MFA, validade e hash das evidências", "met": approved},
        *(
            [
                {
                    "label": "Aprovado para o perfil em vigor",
                    "met": approved and st in (g.scope.get("seller_types") or []),
                }
            ]
            if sensitive
            else []
        ),
    ]
    return {
        "code": g.code,
        "state": g.state,
        **seller.gate_text(g.code, _texts(g), st),
        "seller_type": st,
        "requirements": requirements,
        "scope": g.scope,
        "approved_by": g.approved_by,
        "valid_until": g.valid_until.isoformat() if g.valid_until else None,
        "evidence_sha256": g.evidence_sha256,
        "current_evidence_sha256": gates.evidence_hash(db, code),
        "policy_version": g.policy_version,
        "evidence": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "reference": e.reference,
                "sha256": e.sha256,
                "notes": e.notes,
                "submitted_by": e.submitted_by,
                "submitted_at": e.submitted_at.isoformat(),
            }
            for e in ev
        ],
    }


def _gate_call(fn, db: Session, code: str):
    from aicommerce.governance import gates

    try:
        fn()
    except gates.GateError as e:
        raise HTTPException(e.http, str(e)) from e
    return _gate_detail(db, code)


@router.get("/gates/{code}")
def gate_detail(code: str, db: Session = Depends(get_db), _: Principal = Depends(require("golive:evaluate"))):
    return _gate_detail(db, code)


@router.post("/gates/{code}/evidence", status_code=status.HTTP_201_CREATED)
def gate_evidence(
    code: str, body: EvidenceIn, db: Session = Depends(get_db), p: Principal = Depends(require("gates:manage"))
):
    from aicommerce.governance import gates

    return _gate_call(
        lambda: gates.add_evidence(
            db,
            code,
            kind=body.kind,
            reference=body.reference,
            sha256=body.sha256,
            notes=body.notes,
            actor=str(p.user_id),
        ),
        db,
        code,
    )


@router.post("/gates/{code}/submit")
def gate_submit(code: str, db: Session = Depends(get_db), p: Principal = Depends(require("gates:manage"))):
    from aicommerce.governance import gates

    return _gate_call(lambda: gates.submit(db, code, actor=str(p.user_id)), db, code)


@router.post("/gates/{code}/decide")
def gate_decide(
    code: str, body: GateDecisionIn, db: Session = Depends(get_db), p: Principal = Depends(require("gates:approve"))
):
    from aicommerce.governance import gates

    return _gate_call(
        lambda: gates.decide(
            db,
            code,
            decision=body.decision,
            scope=body.scope,
            valid_until=body.valid_until,
            note=body.note,
            actor=str(p.user_id),
            mfa=p.mfa,
            policy_version=service.active_policy(db).version,
        ),
        db,
        code,
    )


@router.post("/gates/{code}/revoke")
def gate_revoke(
    code: str, body: GateRevokeIn, db: Session = Depends(get_db), p: Principal = Depends(require("gates:approve"))
):
    from aicommerce.governance import gates

    return _gate_call(lambda: gates.revoke(db, code, actor=str(p.user_id), mfa=p.mfa, reason=body.reason), db, code)


# ─────────────────────── perfil do vendedor (PF/PJ) e elegibilidade por canal — ADR 0006 ───────────────────────
class SellerProfileIn(BaseModel):
    person_type: Literal["PERSON", "COMPANY"] | None = None
    uf: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    fiscal_model: str | None = Field(default=None, max_length=32)
    restrictions: list[str] | None = Field(default=None, max_length=20)


class AspectIn(BaseModel):
    status: Literal["SUPPORTED", "CONDITIONAL", "UNVERIFIED", "UNSUPPORTED"]
    note: str = Field(default="", max_length=500)


class SourceIn(BaseModel):
    url: str = Field(max_length=500)
    kind: str = Field(default="", max_length=200)
    accessed: str = Field(default="", max_length=32)


class ConditionIn(BaseModel):
    field: Literal["ufs", "fulfillment_modes", "supplier_origins", "product_ids", "supplier_ids"]
    allowed: list[str] = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=300)


class EligibilityIn(BaseModel):
    seller_type: Literal["PERSON", "COMPANY"]
    conditions: list[ConditionIn] = Field(default_factory=list, max_length=10)
    aspects: dict[str, AspectIn]
    restrictions: list[str] = Field(default_factory=list, max_length=20)
    pending: list[str] = Field(default_factory=list, max_length=20)
    sources: list[SourceIn] = Field(default_factory=list, max_length=10)
    scope: str = Field(default="", max_length=500)


def _seller_call(fn):
    from aicommerce.governance import seller

    try:
        return fn()
    except seller.SellerError as e:
        raise HTTPException(e.http, str(e)) from e


@router.get("/seller/profile")
def get_seller_profile(db: Session = Depends(get_db), _: Principal = Depends(require("golive:evaluate"))):
    from aicommerce.governance import seller

    return seller.profile_out(db)


@router.put("/seller/profile")
def put_seller_profile(
    body: SellerProfileIn, db: Session = Depends(get_db), p: Principal = Depends(require("gates:approve"))
):
    from aicommerce.governance import seller

    changes = body.model_dump(exclude_none=True)
    if "uf" in changes:
        changes["uf"] = changes["uf"].upper()
    _p, lost = _seller_call(lambda: seller.update_profile(db, actor=str(p.user_id), mfa=p.mfa, changes=changes))
    return {**seller.profile_out(db), "gates_lost": lost}


@router.get("/seller/eligibility")
def get_eligibility(
    seller_type: Literal["PERSON", "COMPANY"] | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require("golive:evaluate")),
):
    from aicommerce.governance import seller

    return {
        "seller_type": seller_type or seller.get_profile(db).person_type,
        "aspects": seller.ASPECTS,
        "items": seller.eligibility_out(db, seller_type),
    }


@router.put("/seller/eligibility/{channel}")
def put_eligibility(
    channel: golive.Channel,
    body: EligibilityIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("gates:approve")),
):
    from aicommerce.governance import seller

    _row, lost = _seller_call(
        lambda: seller.update_eligibility(
            db,
            channel.value,
            body.seller_type,
            actor=str(p.user_id),
            mfa=p.mfa,
            aspects={k: v.model_dump() for k, v in body.aspects.items()},
            restrictions=body.restrictions,
            pending=body.pending,
            sources=[s.model_dump() for s in body.sources],
            scope=body.scope,
            conditions=[c.model_dump() for c in body.conditions],
        )
    )
    item = next(i for i in seller.eligibility_out(db, body.seller_type) if i["channel"] == channel.value)
    return {**item, "gates_lost": lost}
