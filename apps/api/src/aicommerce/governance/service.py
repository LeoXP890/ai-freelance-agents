"""Orquestra Policy Engine + GoLivePolicy + persistência da ActionProposal (SDD §8, §14)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.canonical import sha256_of
from aicommerce.config import get_settings
from aicommerce.governance import audit, golive, killswitch, policy, seller
from aicommerce.models import ApprovalProposal, CertificationGate, PolicyVersion
from aicommerce.office import events

STATUS_FOR = {
    policy.Decision.ALLOW: "AUTO_AUTHORIZED",
    policy.Decision.REQUIRE_APPROVAL: "PENDING_APPROVAL",
    policy.Decision.REQUEST_CHANGES: "CHANGES_REQUESTED",
    policy.Decision.DENY: "DENIED",
}


class ProposalConflict(Exception):
    pass


def active_policy(db: Session) -> PolicyVersion:
    pv = (
        db.execute(
            select(PolicyVersion).where(PolicyVersion.is_active.is_(True)).order_by(PolicyVersion.created_at.desc())
        )
        .scalars()
        .first()
    )
    if pv is None:
        raise RuntimeError("nenhuma policy_version ativa — execute o seed")
    return pv


def gate_snapshots(db: Session) -> dict[str, golive.GateSnapshot]:
    return {
        g.code: golive.GateSnapshot(
            g.code, g.state, g.approved_by, g.evidence_sha256, g.policy_version, g.valid_until, g.scope
        )
        for g in db.execute(select(CertificationGate)).scalars()
    }


def evaluate_golive(
    db: Session, req: golive.GoLiveRequest, *, actor: str, request_id: str | None
) -> golive.GoLiveResult:
    res = golive.evaluate(req, gate_snapshots(db), seller.context(db))
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="go_live.evaluated",
        subject_type="channel",
        subject_id=req.channel.value,
        request_id=request_id,
        payload={
            "request": {k: getattr(v, "value", v) for k, v in vars(req).items()},
            "decision": res.decision,
            "unmet": res.unmet,
            "seller_type": res.seller_type,
        },
    )
    return res


def submit_proposal(
    db: Session, data: dict[str, Any], *, actor: str, request_id: str | None, golive_req: golive.GoLiveRequest | None
) -> tuple[ApprovalProposal, bool]:
    """Avalia e persiste. Mesma idempotency_key + mesmo payload = replay; payload diferente = conflito."""
    payload_hash = sha256_of(data["payload"])
    existing = db.execute(
        select(ApprovalProposal).where(ApprovalProposal.idempotency_key == data["idempotency_key"])
    ).scalar_one_or_none()
    if existing:
        if existing.payload_sha256 != payload_hash or existing.action_type != data["action_type"]:
            raise ProposalConflict("idempotency_key reutilizada com payload diferente")
        return existing, False

    s = get_settings()
    pv = active_policy(db)
    gl = None
    if golive_req is not None:
        gl = golive.evaluate(golive_req, gate_snapshots(db), seller.context(db)).decision
    ctx = policy.PolicyContext(
        emergency_stop=killswitch.is_active(db), golive_decision=gl, delegations=killswitch.delegations(db)
    )
    risk_score = policy.RISK_SCORES[data["risk"]]
    pin = policy.PolicyInput(
        action_type=data["action_type"],
        risk_score=risk_score,
        evidence_refs=data["evidence_refs"],
        monetary_scope_minor=data["monetary_scope_minor"],
        currency=data["currency"],
        mode=s.mode.value,
    )
    res = policy.evaluate(pin, ctx, pv.rules, pv.version)
    if gl == "DENY" and s.mode.value == "DRY_RUN":
        # Em DRY_RUN a execução é simulada, mas registramos que o equivalente LIVE seria negado.
        res.reasons.append("equivalente LIVE seria DENY: gates de go-live pendentes")
    prop = ApprovalProposal(
        action_type=data["action_type"],
        agent_id=data.get("agent_id"),
        subject_type=data["subject_type"],
        subject_id=data["subject_id"],
        subject_version=data["subject_version"],
        payload=data["payload"],
        payload_sha256=payload_hash,
        risk_score=risk_score,
        evidence_refs=data["evidence_refs"],
        monetary_scope_minor=data["monetary_scope_minor"],
        currency=data["currency"],
        channel_account_id=data.get("channel_account_id"),
        mode=s.mode.value,
        policy_version=pv.version,
        policy_decision=res.decision.value,
        policy_reasons=res.reasons,
        status=STATUS_FOR[res.decision],
        idempotency_key=data["idempotency_key"],
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=pv.rules["proposal_ttl_hours"]),
        created_by=actor,
        budget_account_id=data.get("budget_account_id"),
        go_live_context=(
            {k: getattr(v, "value", v) for k, v in vars(golive_req).items()} if golive_req is not None else None
        ),
    )
    db.add(prop)
    db.flush()
    from aicommerce.governance import approvals  # import tardio: approvals depende deste módulo

    superseded = approvals.supersede_older(db, prop)
    if prop.status == "AUTO_AUTHORIZED" and approvals.has_external_effect(prop.action_type):
        # Autorização pela política (ex.: pausa delegada): mesmo pipeline de execução, sem reserva.
        approvals.create_execution(db, prop, issued_by=f"policy:{pv.version}", reservation_id=None)
        prop.status = "AUTHORIZED"
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="proposal.evaluated",
        subject_type="approval_proposal",
        subject_id=str(prop.id),
        request_id=request_id,
        payload={
            "decision": res.decision.value,
            "reasons": res.reasons,
            "policy_version": pv.version,
            "payload_sha256": payload_hash,
            "superseded": [str(x) for x in superseded],
        },
    )
    if prop.status == "PENDING_APPROVAL":
        events.emit(
            db,
            "approval.requested",
            agent_id=prop.agent_id,
            state="WAITING_APPROVAL" if prop.agent_id else None,
            payload={"proposal_id": str(prop.id), "action_type": prop.action_type},
        )
    return prop, True
