"""Executor de side effects (SDD §6, §8, §23.1; GOV-004; REQ-DEV-008).

Pipeline por tentativa: preflight → policy → authorization → reservation → external action →
reconcile → ledger/audit → event.

Fronteiras de transação (cada uma modela um ponto de falha real):
  T1  reivindica o job (SKIP LOCKED), valida tudo, consome a autorização e grava EXECUTING + lease.
  --  chamada externa FORA de transação (pode demorar, cair ou expirar).
  T2  registra a tentativa e o resultado.
Se o processo morrer entre T1 e T2, o job fica EXECUTING com lease vencida e a reconciliação o trata
como UNKNOWN. Retry só acontece depois de consultar o provedor pela idempotency_key; o provedor recebe
sempre a MESMA chave, então nunca prometemos "exatamente uma vez" — evitamos duplicar consultando.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.commerce import channels
from aicommerce.commerce.channels import MARKETPLACES
from aicommerce.db import session_scope
from aicommerce.finance import funding
from aicommerce.governance import approvals, audit, killswitch, policy
from aicommerce.models import (
    ApprovalProposal,
    AuthorizationGrant,
    BudgetReservation,
    ChannelListing,
    ExecutionJob,
    ExternalActionAttempt,
)
from aicommerce.office import events
from aicommerce.providers import registry
from aicommerce.providers.contracts import (
    PERMANENT_ERRORS,
    Capability,
    CommandEnvelope,
    ErrorKind,
    ProviderResult,
    ResultStatus,
)

LEASE = dt.timedelta(minutes=2)
RECONCILE_DELAY = dt.timedelta(seconds=2)
BACKOFF = (dt.timedelta(seconds=5), dt.timedelta(seconds=30), dt.timedelta(minutes=2))
# Permitidas com a fila congelada (emergency stop): pausas e o atendimento de pedidos já contratados (SDD §8).
PAUSE_ACTIONS = ("PAUSE_LISTING", "PAUSE_MEDIA", "SUPPLIER_PURCHASE", "REFUND")
PUBLISH_ACTIONS = ("PUBLISH_LISTING", "UPDATE_LISTING_MATERIAL")


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or dt.datetime.now(dt.UTC)


def _channel(prop: ApprovalProposal) -> str | None:
    return (prop.go_live_context or {}).get("channel") or prop.payload.get("channel")


def _emit(db: Session, prop: ApprovalProposal, type_: str, state: str, **payload) -> None:
    events.emit(
        db,
        type_,
        agent_id=prop.agent_id,
        state=state if prop.agent_id else None,
        payload={"proposal_id": str(prop.id), "action_type": prop.action_type, **payload},
    )


def _commerce_hook(db: Session, prop: ApprovalProposal, *, succeeded: bool, provider_ref: str | None) -> None:
    from aicommerce.commerce import (  # import tardio: commerce depende de governance
        ads,
        listings,
        orders,
    )

    listings.on_execution_result(db, prop, succeeded=succeeded, provider_ref=provider_ref)
    orders.on_execution_result(db, prop, succeeded=succeeded, provider_ref=provider_ref)
    ads.on_execution_result(db, prop, succeeded=succeeded, provider_ref=provider_ref)


def _finish_failed(
    db: Session,
    job: ExecutionJob,
    prop: ApprovalProposal,
    grant: AuthorizationGrant | None,
    kind: ErrorKind,
    message: str,
) -> None:
    job.status, job.error_kind, job.last_error, job.lease_until = "FAILED", kind.value, message, None
    prop.status = "FAILED"
    _commerce_hook(db, prop, succeeded=False, provider_ref=None)
    if grant and grant.reservation_id:
        resv = db.get(BudgetReservation, grant.reservation_id)
        if resv and resv.status == "ACTIVE":
            from aicommerce.finance import ledger

            ledger.release(db, resv.id, actor="executor", reason=f"execução falhou: {kind.value}")
    audit.record(
        db,
        actor_type="system",
        actor_id="executor",
        action="execution.failed",
        subject_type="execution_job",
        subject_id=str(job.id),
        payload={"proposal_id": str(prop.id), "error_kind": kind.value, "message": message, "attempts": job.attempts},
    )
    _emit(db, prop, "execution.failed", "ERROR", error_kind=kind.value, message=message)


def _succeed(db: Session, job: ExecutionJob, prop: ApprovalProposal, result: ProviderResult, via: str) -> None:
    job.status, job.provider_ref, job.error_kind, job.last_error, job.lease_until = (
        "SUCCEEDED",
        result.provider_ref,
        None,
        None,
        None,
    )
    prop.status = "SUCCEEDED"
    _commerce_hook(db, prop, succeeded=True, provider_ref=result.provider_ref)
    audit.record(
        db,
        actor_type="system",
        actor_id="executor",
        action="execution.succeeded",
        subject_type="execution_job",
        subject_id=str(job.id),
        payload={
            "proposal_id": str(prop.id),
            "provider": job.provider,
            "provider_ref": result.provider_ref,
            "via": via,
        },
    )
    type_ = "listing.publish.confirmed" if prop.action_type in PUBLISH_ACTIONS else "execution.succeeded"
    _emit(
        db,
        prop,
        type_,
        "COMPLETED",
        provider_ref=result.provider_ref,
        via=via,
        simulated=bool(result.detail.get("simulated")),
    )


def _attempt(
    db: Session, job: ExecutionJob, *, kind: str, request_id: str, result: ProviderResult, started: dt.datetime
) -> None:
    n = db.execute(
        select(func.count()).select_from(ExternalActionAttempt).where(ExternalActionAttempt.job_id == job.id)
    ).scalar_one()
    db.add(
        ExternalActionAttempt(
            job_id=job.id,
            attempt_no=n + 1,
            kind=kind,
            request_id=request_id,
            outcome=result.status.value,
            error_kind=result.error_kind.value if result.error_kind else None,
            provider_ref=result.provider_ref,
            detail=result.detail,
            started_at=started,
            finished_at=dt.datetime.now(dt.UTC),
        )
    )


def _preflight(
    db: Session, job: ExecutionJob, prop: ApprovalProposal, grant: AuthorizationGrant | None, now: dt.datetime
) -> tuple[ErrorKind, str] | None:
    if prop.status != "AUTHORIZED":
        return ErrorKind.AUTHORIZATION_INVALID, f"proposta em estado {prop.status}"
    if now >= job.deadline_at:
        return ErrorKind.DEADLINE_EXCEEDED, "prazo de execução vencido"
    if grant is None or grant.revoked_at is not None:
        return ErrorKind.AUTHORIZATION_INVALID, "autorização revogada ou inexistente"
    if grant.expires_at <= now:
        return ErrorKind.AUTHORIZATION_INVALID, "autorização expirada"
    if (grant.action_type, grant.payload_sha256, grant.subject_version) != (
        prop.action_type,
        prop.payload_sha256,
        prop.subject_version,
    ):
        return ErrorKind.AUTHORIZATION_INVALID, "escopo da autorização não corresponde à proposta"
    if prop.monetary_scope_minor > grant.max_monetary_minor:
        return ErrorKind.AUTHORIZATION_INVALID, "valor acima do teto autorizado"
    if grant.consumed_at is not None and job.attempts == 0:
        return ErrorKind.AUTHORIZATION_INVALID, "autorização de uso único já consumida"
    res = approvals.reevaluate(db, prop)
    if res.decision in (policy.Decision.DENY, policy.Decision.REQUEST_CHANGES):
        return ErrorKind.POLICY_DENIED, "; ".join(res.reasons)
    # O prazo entre decisão do proprietário e o job pode invalidar estoque, fornecedor
    # ou preço. A autorização não substitui a revalidação dos fatos no momento da execução.
    if prop.action_type in PUBLISH_ACTIONS and prop.subject_type == "listing":
        try:
            listing_id = uuid.UUID(prop.subject_id)
        except ValueError:
            if job.mode == "LIVE":
                return ErrorKind.POLICY_DENIED, "publicação LIVE exige anúncio do catálogo identificado"
        else:
            # Evita inverter a ordem de locks: prepare() trava listing antes da proposta,
            # enquanto este executor já travou proposta/autorização.
            listing = db.get(ChannelListing, listing_id)
            if listing is None:
                if job.mode == "LIVE":
                    return ErrorKind.POLICY_DENIED, "anúncio do catálogo não encontrado"
            else:
                if listing.version != prop.subject_version:
                    return ErrorKind.POLICY_DENIED, "versão do anúncio mudou depois da aprovação"
                from aicommerce.commerce import listings

                try:
                    listings.ensure_publish_ready(db, listing)
                except listings.ListingError as exc:
                    return ErrorKind.POLICY_DENIED, f"anúncio não está mais pronto: {exc}"
    if prop.monetary_scope_minor > 0 and prop.action_type in policy.POLICY_CURRENT_RULES["budgeted_actions"]:
        resv = db.get(BudgetReservation, grant.reservation_id) if grant.reservation_id else None
        if resv is None or resv.status != "ACTIVE":
            return ErrorKind.RESERVATION_INVALID, "reserva de orçamento ausente ou inativa"
    return None


def execute_next(now: dt.datetime | None = None) -> bool:
    """Executa no máximo um job pronto. Retorna True se havia trabalho."""
    now = _now(now)
    with session_scope() as db:
        q = (
            select(ExecutionJob)
            .where(ExecutionJob.status.in_(("QUEUED", "RETRY")), ExecutionJob.next_attempt_at <= now)
            .order_by(ExecutionJob.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if killswitch.is_active(db):
            q = q.where(ExecutionJob.action_type.in_(PAUSE_ACTIONS))
        job = db.execute(q).scalar_one_or_none()
        if job is None:
            return False
        prop = db.get(ApprovalProposal, job.proposal_id, with_for_update=True)
        grant = db.get(AuthorizationGrant, job.grant_id, with_for_update=True)
        failure = _preflight(db, job, prop, grant, now)
        if failure:
            _finish_failed(db, job, prop, grant, *failure)
            return True
        channel = _channel(prop)
        if (
            channel in MARKETPLACES
            and job.action_type in registry.ACTION_OPERATION
            and registry.ACTION_OPERATION[job.action_type][0] == "commerce"
        ):
            try:
                channels.valid_access_token(db, channel, now)  # renova antes de usar; nunca vai para o payload
            except channels.TokenRefreshTransient as e:
                job.attempts += 1  # conta como tentativa: canal fora do ar não pode prender o job para sempre
                if job.attempts >= job.max_attempts:
                    _finish_failed(db, job, prop, grant, ErrorKind.TRANSIENT, str(e))
                else:
                    _schedule_retry(db, job, prop, ErrorKind.TRANSIENT)  # renovação falhou por indisponibilidade
                return True
            except channels.TokenUnavailable as e:
                _finish_failed(db, job, prop, grant, ErrorKind.TOKEN_EXPIRED, str(e))
                return True
        res = registry.resolve(job.action_type, job.mode, channel, _approved_gates(db), channels.live_accounts(db))
        if res.provider is None or res.capability is not Capability.SUPPORTED:
            _finish_failed(db, job, prop, grant, ErrorKind.UNSUPPORTED, res.reason)
            return True
        if job.action_type == "SUPPLIER_PURCHASE" and funding.enabled():
            # Caixa real (ADR 0008) antes de falar com o fornecedor; intenção aberta na mesma transação do EXECUTING.
            blocked = funding.open_for_proposal(db, prop, deadline=job.deadline_at)
            if blocked:
                _finish_failed(db, job, prop, grant, ErrorKind.RESERVATION_INVALID, blocked)
                return True
        job.status, job.provider, job.lease_until = "EXECUTING", res.provider.name, now + LEASE
        job.attempts += 1
        prop.status = "EXECUTING"
        if grant.consumed_at is None:
            grant.consumed_at = now
        cmd = CommandEnvelope(
            request_id=uuid.uuid4().hex,
            idempotency_key=job.idempotency_key,
            mode=job.mode,
            capability=res.capability,
            approval_token_ref=str(grant.id),
            expected_version=prop.subject_version,
            operation=res.operation,
            payload=dict(prop.payload),
            attempt=job.attempts,
        )
        if prop.action_type in PUBLISH_ACTIONS:
            _emit(db, prop, "listing.publish.requested", "TOOL_CALL", provider=res.provider.name, attempt=job.attempts)
        job_id, provider = job.id, res.provider

    started = dt.datetime.now(dt.UTC)
    try:
        result = provider.execute(cmd)
    except Exception as e:
        result = ProviderResult(
            ResultStatus.UNKNOWN, error_kind=ErrorKind.TIMEOUT, detail={"exception": type(e).__name__}
        )

    with session_scope() as db:
        job = db.get(ExecutionJob, job_id, with_for_update=True)
        prop = db.get(ApprovalProposal, job.proposal_id, with_for_update=True)
        grant = db.get(AuthorizationGrant, job.grant_id)
        _attempt(db, job, kind="EXECUTE", request_id=cmd.request_id, result=result, started=started)
        funding.settle_for_proposal(
            db,
            prop,
            outcome=result.status.value,
            event_key=f"exec:{job.id}:{job.attempts}",
            provider_ref=result.provider_ref,
        )
        if result.status is ResultStatus.SUCCEEDED:
            _succeed(db, job, prop, result, via="execute")
        elif result.status is ResultStatus.UNKNOWN:
            job.status, job.error_kind, job.lease_until = (
                "UNKNOWN",
                (result.error_kind or ErrorKind.TIMEOUT).value,
                None,
            )
            job.next_attempt_at = dt.datetime.now(dt.UTC) + RECONCILE_DELAY
            prop.status = "UNKNOWN"
            audit.record(
                db,
                actor_type="system",
                actor_id="executor",
                action="execution.unknown",
                subject_type="execution_job",
                subject_id=str(job.id),
                payload={"attempt": job.attempts},
            )
            _emit(db, prop, "integration.unknown_result", "BLOCKED", attempt=job.attempts)
        else:
            kind = result.error_kind or ErrorKind.TRANSIENT
            if kind in PERMANENT_ERRORS or job.attempts >= job.max_attempts:
                _finish_failed(db, job, prop, grant, kind, str(result.detail.get("reason", kind.value)))
            else:
                _schedule_retry(db, job, prop, kind)
    return True


def _schedule_retry(db: Session, job: ExecutionJob, prop: ApprovalProposal, kind: ErrorKind) -> None:
    job.status, job.error_kind, job.lease_until = "RETRY", kind.value, None
    job.next_attempt_at = dt.datetime.now(dt.UTC) + BACKOFF[min(max(job.attempts - 1, 0), len(BACKOFF) - 1)]
    prop.status = "AUTHORIZED"
    audit.record(
        db,
        actor_type="system",
        actor_id="executor",
        action="execution.retry_scheduled",
        subject_type="execution_job",
        subject_id=str(job.id),
        payload={"attempt": job.attempts, "error_kind": kind.value, "next_attempt_at": job.next_attempt_at.isoformat()},
    )


def _approved_gates(db: Session) -> frozenset[str]:
    """Gates de go-live aprovados pelo proprietário (estado atual do banco, consultado a cada execução)."""
    from aicommerce.governance.gates import APPROVED
    from aicommerce.models import CertificationGate

    return frozenset(db.execute(select(CertificationGate.code).where(CertificationGate.state.in_(APPROVED))).scalars())


def reconcile_next(now: dt.datetime | None = None) -> bool:
    """Resolve um job UNKNOWN (ou EXECUTING com lease vencida) consultando o provedor."""
    now = _now(now)
    with session_scope() as db:
        job = db.execute(
            select(ExecutionJob)
            .where(
                (
                    (ExecutionJob.status == "UNKNOWN")
                    & (ExecutionJob.next_attempt_at <= now)
                    # outro reconciliador com lease vigente já está consultando este job (auditoria A04)
                    & (ExecutionJob.lease_until.is_(None) | (ExecutionJob.lease_until < now))
                )
                | ((ExecutionJob.status == "EXECUTING") & (ExecutionJob.lease_until < now))
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            return False
        if job.status == "EXECUTING":  # worker morreu durante a chamada externa
            job.status, job.error_kind, job.lease_until = "UNKNOWN", "LEASE_EXPIRED", None
        prop = db.get(ApprovalProposal, job.proposal_id)
        res = registry.resolve(
            job.action_type, job.mode, _channel(prop), _approved_gates(db), channels.live_accounts(db)
        )
        job_id, key, payload = job.id, job.idempotency_key, dict(prop.payload)
        lease = now + LEASE + dt.timedelta(microseconds=uuid.uuid4().int % 1000)  # valor único = posse da lease
        job.lease_until = lease  # evita reconciliação concorrente do mesmo job

    started = dt.datetime.now(dt.UTC)
    found = res.provider.lookup(key, payload) if res.provider is not None else None
    request_id = uuid.uuid4().hex

    with session_scope() as db:
        job = db.get(ExecutionJob, job_id, with_for_update=True)
        if job.status != "UNKNOWN" or job.lease_until != lease:
            return True  # CAS: outro processo concluiu ou retomou este job; não sobrescreve o resultado dele
        prop = db.get(ApprovalProposal, job.proposal_id, with_for_update=True)
        grant = db.get(AuthorizationGrant, job.grant_id)
        job.lease_until = None
        funding.settle_for_proposal(
            db,
            prop,
            outcome="SUCCEEDED" if found is not None else "FAILED",  # provedor confirmou presença ou ausência
            event_key=f"reconcile:{job.id}:{request_id}",
            provider_ref=found.provider_ref if found is not None else None,
        )
        if found is not None:
            _attempt(db, job, kind="RECONCILE", request_id=request_id, result=found, started=started)
            _succeed(db, job, prop, found, via="reconcile")
        else:
            absent = ProviderResult(
                ResultStatus.FAILED,
                error_kind=ErrorKind.TRANSIENT,
                detail={"reconcile": "efeito não encontrado no provedor"},
            )
            _attempt(db, job, kind="RECONCILE", request_id=request_id, result=absent, started=started)
            if job.attempts >= job.max_attempts:
                _finish_failed(
                    db, job, prop, grant, ErrorKind.TIMEOUT, "sem confirmação do provedor após o limite de tentativas"
                )
            else:
                # Provedor confirmou ausência do efeito: repetir com a MESMA chave é seguro.
                _schedule_retry(db, job, prop, ErrorKind.TIMEOUT)
                job.next_attempt_at = dt.datetime.now(dt.UTC)
    return True


def sweep_expired(now: dt.datetime | None = None, limit: int = 50) -> int:
    now = _now(now)
    with session_scope() as db:
        props = (
            db.execute(
                select(ApprovalProposal)
                .where(
                    ApprovalProposal.status.in_(("PENDING_APPROVAL", "AUTHORIZED")), ApprovalProposal.expires_at <= now
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        return sum(approvals.expire_if_due(db, p, now) for p in props)
