"""Decisões humanas sobre ActionProposals (SDD §8, GOV-002) e criação do job de execução (GOV-004).

* A aprovação é vinculada ao payload exato exibido: payload_sha256 + subject_version precisam bater.
* Quem propôs não aprova a própria proposta (segregação de funções).
* Na aprovação a política é reavaliada com o contexto atual (kill switch, gates LIVE).
* Aprovar cria, na MESMA transação: decisão, reserva de orçamento (se monetária), autorização de uso
  único e execution_job (outbox). Ou tudo existe, ou nada.
* Nova versão do mesmo assunto expira propostas anteriores ainda não executadas.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.finance import ledger
from aicommerce.governance import audit, golive, killswitch, policy
from aicommerce.identity.security import Principal
from aicommerce.models import (
    ApprovalDecision,
    ApprovalProposal,
    AuthorizationGrant,
    BudgetReservation,
    ExecutionJob,
)
from aicommerce.office import events
from aicommerce.providers.registry import ACTION_OPERATION

GRANT_TTL = dt.timedelta(hours=24)
VERDICTS = {"APPROVE": "AUTHORIZED", "REJECT": "REJECTED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}
OPEN_STATUSES = ("PENDING_APPROVAL", "AUTHORIZED", "AUTO_AUTHORIZED")


class ApprovalError(Exception):
    code = "APPROVAL_ERROR"
    http = 409


class NotFound(ApprovalError):
    code, http = "NOT_FOUND", 404


class Forbidden(ApprovalError):
    code, http = "FORBIDDEN", 403


class StalePayload(ApprovalError):
    code = "STALE_PAYLOAD"


class InvalidState(ApprovalError):
    code = "INVALID_STATE"


class PolicyChanged(ApprovalError):
    code = "POLICY_CHANGED"


class BudgetUnavailable(ApprovalError):
    code = "BUDGET_UNAVAILABLE"


class IdempotencyConflict(ApprovalError):
    code = "IDEMPOTENCY_CONFLICT"


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


AGENT_RESULT_ACTIONS = frozenset({"PUBLISH_VIA_PLATFORM"})


def has_external_effect(action_type: str) -> bool:
    return action_type in policy.POLICY_CURRENT_RULES["external_effect_actions"]


def reevaluate(db: Session, prop: ApprovalProposal) -> policy.PolicyResult:
    """Mesma função do momento da proposta, com o contexto ATUAL (dupla validação)."""
    from aicommerce.governance.service import active_policy, gate_snapshots

    pv = active_policy(db)
    gl, gl_unmet = None, {}
    if prop.go_live_context:
        c = prop.go_live_context
        req = golive.GoLiveRequest(
            golive.Channel(c["channel"]),
            golive.SupplierOrigin(c["supplier_origin"]),
            golive.FulfillmentMode(c["fulfillment_mode"]),
            c["uf"],
            c.get("product_id"),
            c.get("supplier_id"),
        )
        from aicommerce.governance import seller

        glr = golive.evaluate(req, gate_snapshots(db), seller.context(db))
        gl, gl_unmet = glr.decision, glr.unmet
    ctx = policy.PolicyContext(
        emergency_stop=killswitch.is_active(db), golive_decision=gl, delegations=killswitch.delegations(db)
    )
    pin = policy.PolicyInput(
        prop.action_type, prop.risk_score, list(prop.evidence_refs), prop.monetary_scope_minor, prop.currency, prop.mode
    )
    res = policy.evaluate(pin, ctx, pv.rules, pv.version)
    if (
        res.decision == policy.Decision.DENY and gl_unmet
    ):  # motivos detalhados (gate/elegibilidade/condição) para a auditoria da execução
        res.reasons.extend(f"go-live {k}: {v}" for k, v in sorted(gl_unmet.items()))
    return res


def _release_reservation(db: Session, grant: AuthorizationGrant | None, actor: str, reason: str) -> None:
    if grant and grant.reservation_id:
        resv = db.get(BudgetReservation, grant.reservation_id)
        if resv and resv.status == "ACTIVE":
            ledger.release(db, resv.id, actor=actor, reason=reason)


def _grant_for(db: Session, proposal_id: uuid.UUID) -> AuthorizationGrant | None:
    return db.execute(
        select(AuthorizationGrant).where(AuthorizationGrant.proposal_id == proposal_id)
    ).scalar_one_or_none()


def _job_for(db: Session, proposal_id: uuid.UUID) -> ExecutionJob | None:
    return db.execute(select(ExecutionJob).where(ExecutionJob.proposal_id == proposal_id)).scalar_one_or_none()


def close_unstarted(db: Session, prop: ApprovalProposal, *, status: str, actor: str, reason: str) -> None:
    """Encerra proposta ainda não executada: cancela job, revoga autorização e libera reserva."""
    job = _job_for(db, prop.id)
    if job and job.status not in ("QUEUED", "RETRY"):
        raise InvalidState(f"execução já iniciada ({job.status}); não pode ser encerrada")
    grant = _grant_for(db, prop.id)
    if job:
        job.status, job.error_kind, job.last_error = "CANCELLED", None, reason
    if grant and grant.revoked_at is None:
        grant.revoked_at = now()
    _release_reservation(db, grant, actor, reason)
    prop.status = status
    _order_hook(db, prop)
    audit.record(
        db,
        actor_type="user" if actor != "system" else "system",
        actor_id=actor,
        action=f"proposal.{status.lower()}",
        subject_type="approval_proposal",
        subject_id=str(prop.id),
        payload={"reason": reason},
    )
    events.emit(
        db,
        "approval.expired" if status == "EXPIRED" else "approval.decided",
        agent_id=prop.agent_id,
        state="IDLE" if prop.agent_id else None,
        payload={"proposal_id": str(prop.id), "status": status, "reason": reason},
    )


def _order_hook(db: Session, prop: ApprovalProposal) -> None:
    from aicommerce.commerce import orders  # import tardio: commerce depende de governance

    orders.on_closed_without_execution(db, prop)


def expire_if_due(db: Session, prop: ApprovalProposal, at: dt.datetime | None = None) -> bool:
    if prop.status in ("PENDING_APPROVAL", "AUTHORIZED") and prop.expires_at <= (at or now()):
        job = _job_for(db, prop.id)
        if job is None or job.status in ("QUEUED", "RETRY"):
            close_unstarted(db, prop, status="EXPIRED", actor="system", reason="prazo de validade vencido")
            return True
    return False


def supersede_older(db: Session, new: ApprovalProposal) -> list[uuid.UUID]:
    """Se payload/versão muda, aprovação expira (SDD §8)."""
    older = (
        db.execute(
            select(ApprovalProposal)
            .where(
                ApprovalProposal.subject_type == new.subject_type,
                ApprovalProposal.subject_id == new.subject_id,
                ApprovalProposal.action_type == new.action_type,
                ApprovalProposal.id != new.id,
                ApprovalProposal.subject_version < new.subject_version,
                ApprovalProposal.status.in_(("PENDING_APPROVAL", "AUTHORIZED")),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    expired = []
    for p in older:
        job = _job_for(db, p.id)
        if job is None or job.status in ("QUEUED", "RETRY"):
            close_unstarted(
                db,
                p,
                status="EXPIRED",
                actor="system",
                reason=f"substituída pela versão {new.subject_version} ({new.id})",
            )
            expired.append(p.id)
    return expired


def create_execution(
    db: Session, prop: ApprovalProposal, *, issued_by: str, reservation_id: uuid.UUID | None
) -> ExecutionJob:
    """Autorização de uso único + job (outbox), na transação do chamador."""
    expires = min(prop.expires_at, now() + GRANT_TTL)
    grant = AuthorizationGrant(
        proposal_id=prop.id,
        action_type=prop.action_type,
        payload_sha256=prop.payload_sha256,
        subject_version=prop.subject_version,
        max_monetary_minor=prop.monetary_scope_minor,
        reservation_id=reservation_id,
        issued_by=issued_by,
        expires_at=expires,
    )
    db.add(grant)
    db.flush()
    job = ExecutionJob(
        proposal_id=prop.id,
        grant_id=grant.id,
        action_type=prop.action_type,
        mode=prop.mode,
        status="QUEUED",
        idempotency_key=f"exec:{prop.id}",
        deadline_at=expires,
        next_attempt_at=now(),
    )
    db.add(job)
    db.flush()
    return job


def decide(
    db: Session,
    proposal_id: uuid.UUID,
    *,
    decision: str,
    principal: Principal,
    payload_sha256: str,
    subject_version: int,
    comment: str,
    idempotency_key: str,
    request_id: str | None,
) -> tuple[ApprovalProposal, bool]:
    """Retorna (proposta, criada_agora). Replay com a mesma chave devolve o resultado anterior."""
    prior = db.execute(
        select(ApprovalDecision).where(ApprovalDecision.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if prior:
        if prior.proposal_id != proposal_id or prior.decision != decision:
            raise IdempotencyConflict("idempotency_key já usada para outra decisão")
        return db.get(ApprovalProposal, proposal_id), False

    prop = db.get(ApprovalProposal, proposal_id, with_for_update=True)
    if prop is None:
        raise NotFound("proposta inexistente")
    actor = str(principal.user_id)
    if not principal.mfa:
        raise Forbidden("decisão exige sessão com MFA")
    if prop.created_by == actor:
        raise Forbidden("quem propôs não pode decidir a própria proposta")
    if expire_if_due(db, prop):
        raise InvalidState("proposta expirada")
    if prop.status != "PENDING_APPROVAL":
        raise InvalidState(f"proposta em estado {prop.status}")
    if (payload_sha256, subject_version) != (prop.payload_sha256, prop.subject_version):
        raise StalePayload("o conteúdo aprovado difere da versão atual da proposta; recarregue e revise")
    if decision != "APPROVE" and len(comment.strip()) < 3:
        raise ApprovalError("rejeição/pedido de mudança exige comentário")

    reservation_id = None
    if decision == "APPROVE":
        res = reevaluate(db, prop)
        if res.decision in (policy.Decision.DENY, policy.Decision.REQUEST_CHANGES):
            raise PolicyChanged("política atual não permite mais esta ação: " + "; ".join(res.reasons))
        if prop.monetary_scope_minor > 0 and prop.action_type in policy.POLICY_CURRENT_RULES["budgeted_actions"]:
            if prop.budget_account_id is None:
                raise BudgetUnavailable("proposta monetária sem conta de orçamento (budget_account_id)")
            try:
                resv, _ = ledger.reserve(
                    db,
                    prop.budget_account_id,
                    amount_minor=prop.monetary_scope_minor,
                    currency=prop.currency,
                    idempotency_key=f"proposal:{prop.id}",
                    purpose=f"{prop.action_type} {prop.subject_type}:{prop.subject_id}",
                    actor=actor,
                    proposal_id=prop.id,
                    allow_during_emergency_stop=prop.action_type
                    not in policy.POLICY_CURRENT_RULES["blocked_by_emergency_stop"],
                )
            except ledger.LedgerError as e:
                raise BudgetUnavailable(f"{e.code}: {e}") from e
            reservation_id = resv.id

    db.add(
        ApprovalDecision(
            proposal_id=prop.id,
            decision=decision,
            decided_by=principal.user_id,
            payload_sha256=payload_sha256,
            subject_version=subject_version,
            comment=comment.strip(),
            mfa=principal.mfa,
            idempotency_key=idempotency_key,
        )
    )
    prop.status = VERDICTS[decision]
    if decision != "APPROVE":
        _order_hook(db, prop)
    job = None
    # PUBLISH_VIA_PLATFORM não vira job: fica AUTHORIZED até o agente registrar o resultado feito pela plataforma
    # (commerce/platform_publish.record_published). As demais ações externas seguem para o executor.
    if decision == "APPROVE" and has_external_effect(prop.action_type) and prop.action_type not in AGENT_RESULT_ACTIONS:
        job = create_execution(db, prop, issued_by=actor, reservation_id=reservation_id)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action=f"approval.{decision.lower()}",
        subject_type="approval_proposal",
        subject_id=str(prop.id),
        request_id=request_id,
        payload={
            "payload_sha256": payload_sha256,
            "subject_version": subject_version,
            "comment": comment,
            "reservation_id": str(reservation_id) if reservation_id else None,
            "job_id": str(job.id) if job else None,
        },
    )
    events.emit(
        db,
        "approval.decided",
        agent_id=prop.agent_id,
        state=("PLANNING" if decision == "APPROVE" else "IDLE") if prop.agent_id else None,
        payload={"proposal_id": str(prop.id), "decision": decision, "status": prop.status},
    )
    return prop, True


def revoke(
    db: Session,
    proposal_id: uuid.UUID,
    *,
    principal: Principal,
    reason: str,
    idempotency_key: str,
    request_id: str | None,
) -> tuple[ApprovalProposal, bool]:
    prior = db.execute(
        select(ApprovalDecision).where(ApprovalDecision.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if prior:
        if prior.proposal_id != proposal_id or prior.decision != "REVOKE":
            raise IdempotencyConflict("idempotency_key já usada para outra decisão")
        return db.get(ApprovalProposal, proposal_id), False
    prop = db.get(ApprovalProposal, proposal_id, with_for_update=True)
    if prop is None:
        raise NotFound("proposta inexistente")
    if not principal.mfa:
        raise Forbidden("revogação exige sessão com MFA")
    if prop.status not in ("AUTHORIZED", "AUTO_AUTHORIZED"):
        raise InvalidState(f"proposta em estado {prop.status} não pode ser revogada")
    actor = str(principal.user_id)
    close_unstarted(db, prop, status="REVOKED", actor=actor, reason=reason)
    db.add(
        ApprovalDecision(
            proposal_id=prop.id,
            decision="REVOKE",
            decided_by=principal.user_id,
            payload_sha256=prop.payload_sha256,
            subject_version=prop.subject_version,
            comment=reason,
            mfa=principal.mfa,
            idempotency_key=idempotency_key,
        )
    )
    db.flush()
    return prop, True


def summary(db: Session, prop: ApprovalProposal) -> dict[str, str | None]:
    """Resumo legível para a lista de aprovações: objeto afetado, canal, motivo e detalhe. Só dados que existem na
    proposta ou no registro que ela afeta — o que não existe fica None (a interface não inventa)."""
    import uuid as _uuid

    from aicommerce.models import ChannelListing, Product, Variant

    pl = prop.payload or {}
    obj = detail = None
    channel = pl.get("channel") or (prop.go_live_context or {}).get("channel")
    if prop.subject_type == "listing":
        try:
            listing = db.get(ChannelListing, _uuid.UUID(prop.subject_id))
        except ValueError:
            listing = None
        if listing is not None:
            v = db.get(Variant, listing.variant_id)
            p = db.get(Product, v.product_id) if v else None
            obj = p.title if p else None
            detail = v.sku if v else None
            channel = listing.channel
    if not obj:
        obj = next((str(pl[k]) for k in ("title", "name", "item_name") if pl.get(k)), None)
    if not obj and pl.get("order_external_id"):
        obj = f"Pedido {pl['order_external_id']}"
    if pl.get("items") and isinstance(pl["items"], list):  # compra no fornecedor: itens e fornecedor
        detail = ", ".join(f"{i.get('qty')}× {i.get('sku')}" for i in pl["items"][:3] if isinstance(i, dict))
        if pl.get("supplier"):
            detail = f"{pl['supplier']} · {detail}"
    if not detail and pl.get("seller_custom_field"):
        detail = str(pl["seller_custom_field"])
    reason = next((str(pl[k]) for k in ("reason", "why", "rationale") if pl.get(k)), None)
    return {"object": obj, "detail": detail, "channel": channel, "reason": reason[:200] if reason else None}


# Ações que mexem no mesmo "material" do assunto (publicar e atualizar o anúncio) são comparáveis entre si.
COMPARE_GROUP = {"PUBLISH_LISTING": "listing_material", "UPDATE_LISTING_MATERIAL": "listing_material"}
MAX_CHANGES = 60


def _short(v: object) -> object:
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + "…"
    if isinstance(v, (dict, list)):
        import json

        text = json.dumps(v, ensure_ascii=False, sort_keys=True)
        return v if len(text) <= 300 else text[:300] + "…"
    return v


def _diff(before: object, after: object, path: str, out: list[dict]) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after), key=str):
            if str(k).startswith("_"):  # metadados internos não são decisão do aprovador
                continue
            _diff(before.get(k), after.get(k), f"{path}.{k}" if path else str(k), out)
        return
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            _diff(b, a, f"{path}[{i}]", out)
        return
    out.append({"path": path or "(tudo)", "before": _short(before), "after": _short(after)})


def compare(db: Session, prop: ApprovalProposal) -> dict | None:
    """O que esta proposta muda em relação ao que já foi EXECUTADO para o mesmo assunto (ou, se nada foi
    executado, à proposta anterior). Só compara ações do mesmo grupo (publicar × atualizar anúncio, etc.)."""
    group = COMPARE_GROUP.get(prop.action_type, prop.action_type)
    actions = [a for a, g in COMPARE_GROUP.items() if g == group] or [prop.action_type]
    q = (
        select(ApprovalProposal)
        .where(
            ApprovalProposal.subject_type == prop.subject_type,
            ApprovalProposal.subject_id == prop.subject_id,
            ApprovalProposal.action_type.in_(actions),
            ApprovalProposal.id != prop.id,
            ApprovalProposal.created_at <= prop.created_at,
        )
        .order_by(ApprovalProposal.created_at.desc())
        .limit(1)
    )
    base = db.execute(q.where(ApprovalProposal.status == "SUCCEEDED")).scalar_one_or_none()
    executed = base is not None
    if base is None:
        base = db.execute(q).scalar_one_or_none()
    if base is None:
        return None
    changes: list[dict] = []
    _diff(base.payload, prop.payload, "", changes)
    return {
        "base": {
            "id": str(base.id),
            "action_type": base.action_type,
            "status": base.status,
            "subject_version": base.subject_version,
            "created_at": base.created_at.isoformat(),
            "executed": executed,
        },
        "changes": changes[:MAX_CHANGES],
        "truncated": len(changes) > MAX_CHANGES,
    }


def detail(db: Session, prop: ApprovalProposal) -> dict:
    decisions = (
        db.execute(
            select(ApprovalDecision)
            .where(ApprovalDecision.proposal_id == prop.id)
            .order_by(ApprovalDecision.decided_at)
        )
        .scalars()
        .all()
    )
    job = _job_for(db, prop.id)
    grant = _grant_for(db, prop.id)
    return {
        "payload": prop.payload,
        "comparison": compare(db, prop),
        "go_live_context": prop.go_live_context,
        "budget_account_id": str(prop.budget_account_id) if prop.budget_account_id else None,
        "created_by": prop.created_by,
        "executable": prop.action_type in ACTION_OPERATION,
        "decisions": [
            {
                "decision": d.decision,
                "decided_by": str(d.decided_by),
                "comment": d.comment,
                "payload_sha256": d.payload_sha256,
                "decided_at": d.decided_at.isoformat(),
            }
            for d in decisions
        ],
        "grant": None
        if grant is None
        else {
            "id": str(grant.id),
            "expires_at": grant.expires_at.isoformat(),
            "consumed": grant.consumed_at is not None,
            "revoked": grant.revoked_at is not None,
            "reservation_id": str(grant.reservation_id) if grant.reservation_id else None,
        },
        "job": None
        if job is None
        else {
            "id": str(job.id),
            "status": job.status,
            "attempts": job.attempts,
            "provider": job.provider,
            "provider_ref": job.provider_ref,
            "error_kind": job.error_kind,
            "last_error": job.last_error,
        },
    }
