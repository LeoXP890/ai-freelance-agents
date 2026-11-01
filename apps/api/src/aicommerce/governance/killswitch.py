"""Emergency stop (SDD §8, FR-019): trava novas despesas/publicações, revoga delegações e congela filas.

Pedidos já contratados continuam sendo atendidos. Pausas externas são *solicitadas* pela governança:
PAUSE_LISTING para cada anúncio publicado (liberada pela política) e PAUSE_MEDIA para cada campanha ativa
(delegável — como o stop revoga delegações, vai para aprovação humana). Uma pausa só conta como confirmada
quando o job do executor termina com sucesso; o relatório pós-stop mostra o estado real de cada uma.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.governance import audit
from aicommerce.models import SystemFlag

FLAG = "emergency_stop"
DELEGATIONS = "delegations"


def state(db: Session, *, for_update: bool = False) -> dict[str, Any]:
    q = select(SystemFlag).where(SystemFlag.key == FLAG)
    if for_update:
        q = q.with_for_update()
    flag = db.execute(q).scalar_one_or_none()
    return flag.value if flag else {"active": False}


def is_active(db: Session) -> bool:
    return bool(state(db).get("active"))


def delegations(db: Session) -> frozenset[str]:
    flag = db.get(SystemFlag, DELEGATIONS)
    return frozenset(flag.value.get("actions", [])) if flag else frozenset()


def _upsert(db: Session, key: str, value: dict[str, Any], actor: str) -> None:
    flag = db.get(SystemFlag, key, with_for_update=True)
    now = dt.datetime.now(dt.UTC)
    if flag:
        flag.value, flag.updated_by, flag.updated_at = value, actor, now
    else:
        db.add(SystemFlag(key=key, value=value, updated_by=actor, updated_at=now))
    db.flush()


def activate(db: Session, *, actor: str, reason: str, request_id: str | None = None) -> dict[str, Any]:
    from aicommerce.office import events  # import tardio: evita ciclo

    revoked = sorted(delegations(db))
    value = {
        "active": True,
        "reason": reason,
        "activated_by": actor,
        "activated_at": dt.datetime.now(dt.UTC).isoformat(),
        "revoked_delegations": revoked,
        "queues_frozen": True,
        "external_pause_requests": [],
    }
    _upsert(db, FLAG, value, actor)
    _upsert(db, DELEGATIONS, {"actions": []}, actor)  # antes das pausas: PAUSE_MEDIA não pode sair delegada
    value["external_pause_requests"] = _request_pauses(db, actor=actor, request_id=request_id)
    _upsert(db, FLAG, value, actor)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="emergency_stop.activated",
        subject_type="system",
        subject_id=FLAG,
        payload=value,
        request_id=request_id,
    )
    events.emit(db, "emergency_stop.activated", payload={"reason": reason, "revoked_delegations": revoked})
    return value


def release(db: Session, *, actor: str, note: str, request_id: str | None = None) -> dict[str, Any]:
    from aicommerce.office import events

    prev = state(db)  # mantém o histórico da ativação para o relatório pós-stop
    value = {
        **{k: v for k, v in prev.items() if k != "active"},
        "active": False,
        "released_by": actor,
        "released_at": dt.datetime.now(dt.UTC).isoformat(),
        "note": note,
    }
    _upsert(db, FLAG, value, actor)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="emergency_stop.released",
        subject_type="system",
        subject_id=FLAG,
        payload=value,
        request_id=request_id,
    )
    events.emit(db, "emergency_stop.released", payload={"note": note})
    return value


def _request_pauses(db: Session, *, actor: str, request_id: str | None) -> list[dict[str, Any]]:
    from aicommerce.commerce import ads, listings
    from aicommerce.models import Campaign, ChannelListing

    nonce = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%f")
    # Proponente é o sistema: quem acionou o stop continua podendo aprovar a pausa de mídia (sem autoaprovação).
    system, reason = "system:emergency_stop", f"emergency stop acionado por {actor}"
    out: list[dict[str, Any]] = []
    published = db.execute(
        select(ChannelListing).where(ChannelListing.status == "PUBLISHED", ChannelListing.external_id.is_not(None))
    ).scalars()
    for listing in published.all():
        try:
            prop = listings.propose_pause(
                db, listing.id, actor=system, reason=reason, request_id=request_id, nonce=nonce
            )
            out.append(
                {
                    "kind": "LISTING",
                    "subject_id": str(listing.id),
                    "channel": listing.channel,
                    "proposal_id": str(prop.id),
                    "requested_status": prop.status,
                }
            )
        except Exception as e:  # registra e segue: uma falha não pode impedir as demais pausas
            out.append(
                {
                    "kind": "LISTING",
                    "subject_id": str(listing.id),
                    "channel": listing.channel,
                    "proposal_id": None,
                    "requested_status": "REQUEST_FAILED",
                    "error": str(e)[:200],
                }
            )
    for c in db.execute(select(Campaign).where(Campaign.status.in_(("ACTIVE", "ERROR")))).scalars().all():
        try:
            prop = ads.propose_pause(db, c, actor=system, reason=reason, request_id=request_id)
            out.append(
                {
                    "kind": "CAMPAIGN",
                    "subject_id": str(c.id),
                    "channel": c.provider,
                    "proposal_id": str(prop.id),
                    "requested_status": prop.status,
                }
            )
        except Exception as e:
            out.append(
                {
                    "kind": "CAMPAIGN",
                    "subject_id": str(c.id),
                    "channel": c.provider,
                    "proposal_id": None,
                    "requested_status": "REQUEST_FAILED",
                    "error": str(e)[:200],
                }
            )
    return out


CONFIRMED, PENDING, FAILED = "CONFIRMADA", "PENDENTE", "FALHOU"


def report(db: Session) -> dict[str, Any]:
    """Relatório pós-stop: o que foi pedido, o que o canal confirmou, o que segue pendente e o que gastou no período."""
    from aicommerce.models import ApprovalProposal, ExecutionJob, LedgerEntry

    st = state(db)
    if not st.get("activated_at") and not st.get("active"):
        return {"active": False, "message": "kill switch nunca acionado"}
    since = dt.datetime.fromisoformat(st["activated_at"]) if st.get("activated_at") else None
    pauses = []
    for r in st.get("external_pause_requests", []):
        prop = db.get(ApprovalProposal, r["proposal_id"]) if r.get("proposal_id") else None
        job = (
            db.execute(select(ExecutionJob).where(ExecutionJob.proposal_id == prop.id)).scalar_one_or_none()
            if prop
            else None
        )
        if job is not None and job.status == "SUCCEEDED":
            outcome = CONFIRMED
        elif r.get("requested_status") == "REQUEST_FAILED" or (
            prop and prop.status in ("FAILED", "DENIED", "REJECTED")
        ):
            outcome = FAILED
        else:
            outcome = PENDING
        pauses.append(
            {
                **r,
                "proposal_status": prop.status if prop else None,
                "job_status": job.status if job else None,
                "outcome": outcome,
            }
        )
    frozen = db.execute(
        select(ExecutionJob.action_type, ExecutionJob.status).where(ExecutionJob.status.in_(("QUEUED", "RETRY")))
    ).all()
    spend = []
    if since:
        spend = [
            {"amount_minor": e.amount_minor, "external_ref": e.external_ref, "at": e.created_at.isoformat()}
            for e in db.execute(
                select(LedgerEntry).where(LedgerEntry.entry_type == "SPEND", LedgerEntry.created_at >= since)
            ).scalars()
        ]
    counts = {k: sum(1 for p in pauses if p["outcome"] == k) for k in (CONFIRMED, PENDING, FAILED)}
    return {
        "active": bool(st.get("active")),
        "reason": st.get("reason"),
        "activated_by": st.get("activated_by"),
        "activated_at": st.get("activated_at"),
        "released_at": st.get("released_at"),
        "revoked_delegations": st.get("revoked_delegations", []),
        "pauses": pauses,
        "pause_counts": counts,
        "all_paused": counts[CONFIRMED] == len(pauses),
        "frozen_jobs": [{"action_type": a, "status": s_} for a, s_ in frozen],
        "spend_since_stop": spend,
        "spend_since_stop_minor": sum(x["amount_minor"] for x in spend),
        "note": "Gasto após o stop é o que a plataforma já tinha realizado (importado), não nova despesa autorizada.",
    }
