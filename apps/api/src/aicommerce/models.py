"""Modelo lógico da Fase A (SDD §15): Identity, Workforce, Governance, Finance e Office.

Convenções: valores monetários BIGINT em minor units + moeda ISO; timestamps UTC (timestamptz);
tabelas append-only (ledger_entries, audit_events, office_events) são protegidas por trigger na migração.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _pk_uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ───────────────────────────── Identity ─────────────────────────────
class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk_uuid()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(Text)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Segredo gerado no cadastro self-service; só vira totp_secret após confirmação com um código válido.
    totp_pending_secret: Mapped[str | None] = mapped_column(Text)
    # Último passo TOTP aceito: impede reutilizar o mesmo código (replay) dentro da janela de validade.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)
    # Falhas de 2º fator: contador próprio, NÃO zerado pela senha correta (evita contornar o limite).
    failed_mfa_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Incrementado em troca de senha / redefinição de MFA: tokens com versão anterior deixam de valer.
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requires_mfa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_name: Mapped[str] = mapped_column(ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True)


class MfaRecoveryCode(Base):
    """Código de recuperação de MFA: só o SHA-256 é guardado (código aleatório de 80 bits); uso único."""

    __tablename__ = "mfa_recovery_codes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class DeviceSession(Base):
    """Computador lembrado (app desktop): abre sem pedir login. Guarda só o SHA-256 do segredo (256 bits).
    Vale enquanto não revogado e enquanto o session_version do usuário não mudar (senha/MFA)."""

    __tablename__ = "device_sessions"
    id: Mapped[uuid.UUID] = _pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    mfa: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_name: Mapped[str] = mapped_column(ForeignKey("roles.name", ondelete="RESTRICT"), primary_key=True)


# ───────────────────────────── Workforce ─────────────────────────────
class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    room: Mapped[str] = mapped_column(String(32), nullable=False)
    color: Mapped[str] = mapped_column(String(9), nullable=False)
    # Aparência do avatar no escritório (configurável; não afeta comportamento): acessório e tom de pele.
    appearance: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict, server_default="{}")


class AgentConfig(Base):
    __tablename__ = "agent_configs"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    objectives: Mapped[list[Any]] = mapped_column(nullable=False)
    tools: Mapped[list[Any]] = mapped_column(nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_agent_tasks_state_created", "state", "created_at"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )

    __mapper_args__ = {"version_id_col": version}


class AgentRoutine(Base):
    """Tarefa recorrente de um agente (rotina). O worker cria a AgentTask no horário; nada além do que a tarefa faz."""

    __tablename__ = "agent_routines"
    __table_args__ = (Index("ix_agent_routines_due", "enabled", "next_run_at"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    schedule: Mapped[str] = mapped_column(String(16), nullable=False)
    time_of_day: Mapped[str | None] = mapped_column(String(5))
    interval_hours: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_task_id: Mapped[uuid.UUID | None] = mapped_column()
    last_outcome: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class ToolCache(Base):
    """Resultado de ferramenta reaproveitado dentro da validade (economia de chamadas e tokens)."""

    __tablename__ = "tool_cache"
    __table_args__ = (Index("ix_tool_cache_expires", "expires_at"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolCall(Base):
    """Cada chamada de ferramenta por agente: auditoria, limite diário e custo."""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_agent_tool_created", "agent_id", "tool", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(String(200))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    task_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = _created()


class RobotRun(Base):
    """Execução do robô de publicação para uma proposta PUBLISH_VIA_PLATFORM aprovada."""

    __tablename__ = "robot_runs"
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_proposals.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    dropi_edit_url: Mapped[str | None] = mapped_column(String(300))
    listing_id: Mapped[str | None] = mapped_column(String(32))
    listing_url: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


# ───────────────────────────── Governance ─────────────────────────────
class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    rules: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = _created()


class ApprovalProposal(Base):
    __tablename__ = "approval_proposals"
    __table_args__ = (
        CheckConstraint("monetary_scope_minor >= 0", name="ck_proposal_money_nonneg"),
        Index("ix_proposals_status_created", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    monetary_scope_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    channel_account_id: Mapped[str | None] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(ForeignKey("policy_versions.version"), nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_reasons: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128))
    budget_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("money_accounts.id"))
    # Contexto de canal usado no GoLivePolicy; o executor reavalia com ele antes do side effect.
    go_live_context: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[dt.datetime] = _created()


class ApprovalDecision(Base):
    """Decisão humana vinculada ao payload exato (hash + versão) que foi exibido. Append-only."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        Index(
            "uq_one_verdict_per_proposal",
            "proposal_id",
            unique=True,
            postgresql_where=text("decision IN ('APPROVE','REJECT','REQUEST_CHANGES')"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_proposals.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_version: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mfa: Mapped[bool] = mapped_column(Boolean, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    decided_at: Mapped[dt.datetime] = _created()


class AuthorizationGrant(Base):
    """Autorização de uso único, com escopo (ação + hash + versão + teto) e validade."""

    __tablename__ = "authorization_grants"
    id: Mapped[uuid.UUID] = _pk_uuid()
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_proposals.id"), unique=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_version: Mapped[int] = mapped_column(Integer, nullable=False)
    max_monetary_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("budget_reservations.id"))
    issued_by: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class ExecutionJob(Base):
    """Outbox transacional de side effects: criado na mesma transação da autorização."""

    __tablename__ = "execution_jobs"
    __table_args__ = (Index("ix_jobs_status_next", "status", "next_attempt_at"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_proposals.id"), unique=True, nullable=False)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("authorization_grants.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    deadline_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    error_kind: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class ExternalActionAttempt(Base):
    __tablename__ = "external_action_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("execution_jobs.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # EXECUTE | RECONCILE
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SimProviderEffect(Base):
    """Estado do 'mundo externo' simulado (DRY_RUN): o que os provedores mock efetivamente fizeram.

    Persistido para que falhas como 'timeout após o efeito' sejam reconciliáveis entre processos."""

    __tablename__ = "sim_provider_effects"
    __table_args__ = (UniqueConstraint("provider", "idempotency_key"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class CertificationGate(Base):
    __tablename__ = "certification_gates"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    expected_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    responsible: Mapped[str] = mapped_column(Text, nullable=False)
    blocks: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    approved_by: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[dt.datetime] = _created()


class SellerProfile(Base):
    """Perfil jurídico do operador (PF/PJ). Uma linha (id=1). NÃO guarda CPF/CNPJ: o identificador fiscal não é
    necessário para o funcionamento atual; só o estado de verificação é registrado (ADR 0006)."""

    __tablename__ = "seller_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    person_type: Mapped[str] = mapped_column(String(16), nullable=False, default="PERSON")
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="BR")
    uf: Mapped[str | None] = mapped_column(String(2))
    tax_id_status: Mapped[str] = mapped_column(String(24), nullable=False, default="NOT_COLLECTED")
    fiscal_model: Mapped[str] = mapped_column(String(32), nullable=False, default="UNDEFINED")
    restrictions: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    verified_source: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = _created()


class ChannelEligibility(Base):
    """Elegibilidade de um canal para um perfil de vendedor, aspecto por aspecto (cadastro, venda habitual, API,
    venda à ordem, envio, fiscal), com fonte, data e versão. Não autoriza nada sozinha: o go-live exige os gates."""

    __tablename__ = "channel_eligibility"
    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    seller_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNVERIFIED")
    aspects: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    restrictions: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    pending: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    sources: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    # condições verificáveis contra a operação concreta: [{"field": "ufs", "allowed": ["SP"], "note": "…"}]
    conditions: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = _created()


class ResearchCall(Base):
    """Cada chamada (ou bloqueio) de pesquisa externa: teto, intervalo e trilha. Sem chave nem consulta crua."""

    __tablename__ = "research_calls"
    __table_args__ = (
        Index("ix_research_calls_provider_at", "provider", "created_at"),
        CheckConstraint("status IN ('OK','ERROR','BLOCKED','IN_FLIGHT')", name="ck_research_calls_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    query_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    http_status: Mapped[int | None] = mapped_column(Integer)
    results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = _created()


class SystemFlag(Base):
    """Flags operacionais (ex.: emergency_stop). Mudança sempre auditada."""

    __tablename__ = "system_flags"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[dt.datetime] = _created()


class AuditEvent(Base):
    """Append-only com cadeia de hash (prev_hash → hash) para evidência de adulteração."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_subject", "subject_type", "subject_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


# ───────────────────────────── Finance ─────────────────────────────
class MoneyAccount(Base):
    __tablename__ = "money_accounts"
    id: Mapped[uuid.UUID] = _pk_uuid()
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class BudgetPolicy(Base):
    __tablename__ = "budget_policies"
    __table_args__ = (
        CheckConstraint("approved_cap_minor >= 0", name="ck_budget_cap_nonneg"),
        CheckConstraint("safety_hold_minor >= 0", name="ck_budget_hold_nonneg"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("money_accounts.id"), primary_key=True)
    approved_cap_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    safety_hold_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[dt.datetime] = _created()


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_resv_amount_pos"),
        CheckConstraint("consumed_minor >= 0 AND consumed_minor <= amount_minor", name="ck_resv_consumed"),
        Index("ix_resv_account_status", "account_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("money_accounts.id"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    consumed_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_proposals.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_ledger_amount_pos"),
        Index("ix_ledger_account_type", "account_id", "entry_type"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("money_accounts.id"), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("budget_reservations.id"))
    external_ref: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


# Caixa para pagar fornecedor (ADR 0008). Distinto do orçamento acima: aqui só entra saldo observado e transferível.
class CashSource(Base):
    """OPERATING = saldo liberado da operação; RESERVE = fundo de reserva. Sem política aprovada não financia nada."""

    __tablename__ = "cash_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('OPERATING','RESERVE')", name="ck_cash_source_kind"),
        CheckConstraint("floor_minor IS NULL OR floor_minor >= 0", name="ck_cash_source_floor"),
        CheckConstraint("per_order_limit_minor IS NULL OR per_order_limit_minor > 0", name="ck_cash_source_order"),
        CheckConstraint("period_limit_minor IS NULL OR period_limit_minor > 0", name="ck_cash_source_period"),
        CheckConstraint("period_days IS NULL OR period_days > 0", name="ck_cash_source_days"),
        CheckConstraint("max_exposure_minor IS NULL OR max_exposure_minor > 0", name="ck_cash_source_exposure"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    floor_minor: Mapped[int | None] = mapped_column(BigInteger)
    per_order_limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    period_limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    period_days: Mapped[int | None] = mapped_column(Integer)
    max_exposure_minor: Mapped[int | None] = mapped_column(BigInteger)
    policy_approved_by: Mapped[str | None] = mapped_column(String(128))
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = _created()


class CashBalanceSnapshot(Base):
    """Leitura datada do saldo (append-only). available = liberado e transferível; retained nunca é alocado."""

    __tablename__ = "cash_balance_snapshots"
    __table_args__ = (
        CheckConstraint("available_minor >= 0", name="ck_cash_snapshot_available"),
        CheckConstraint("retained_minor >= 0", name="ck_cash_snapshot_retained"),
        Index("ix_cash_snapshot_source_observed", "source_id", "observed_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sources.id"), nullable=False)
    available_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    retained_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class FundingAllocation(Base):
    """Uma obrigação com o fornecedor financiada por até duas fontes. No máximo uma alocação viva por obrigação."""

    __tablename__ = "funding_allocations"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_funding_amount_pos"),
        CheckConstraint(
            "status IN ('BLOCKED','RESERVED','PAYMENT_PENDING','PAYMENT_UNKNOWN','PAID','FAILED','RELEASED')",
            name="ck_funding_status",
        ),
        CheckConstraint("reserve_recovered_minor >= 0", name="ck_funding_recovered"),
        Index(
            "uq_funding_live_obligation",
            "obligation_key",
            unique=True,
            postgresql_where=text("status IN ('RESERVED','PAYMENT_PENDING','PAYMENT_UNKNOWN','PAID')"),
        ),
        Index("ix_funding_status", "status"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    obligation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_proposals.id"))
    quote_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    quote_valid_until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    beneficiary_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    block_reason: Mapped[str | None] = mapped_column(String(48))
    payment_ref: Mapped[str | None] = mapped_column(String(128))
    payment_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    reserve_recovered_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class FundingLeg(Base):
    """Quanto cada fonte cobre da alocação e com qual leitura de saldo (append-only)."""

    __tablename__ = "funding_legs"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_funding_leg_pos"),
        UniqueConstraint("allocation_id", "source_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    allocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("funding_allocations.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sources.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("cash_balance_snapshots.id"), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class FundingEvent(Base):
    """Trilha e deduplicação de cada transição (resultado do provedor, recebível, liberação). Append-only."""

    __tablename__ = "funding_events"
    __table_args__ = (Index("ix_funding_events_allocation", "allocation_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    allocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("funding_allocations.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    external_ref: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


# ───────────────────────────── Office ─────────────────────────────
class AgentPresence(Base):
    __tablename__ = "agent_presence"
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="IDLE")
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[dt.datetime] = _created()


class OfficeEvent(Base):
    __tablename__ = "office_events"
    __table_args__ = (Index("ix_office_events_agent_seq", "agent_id", "seq"),)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str | None] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ───────────────────────────── Commerce (catálogo, fornecedores, canais) ─────────────────────────────
class Product(Base):
    """Catálogo mestre (SDD §9). Canais recebem cópias por listing; o produto canônico é a fonte."""

    __tablename__ = "products"
    id: Mapped[uuid.UUID] = _pk_uuid()
    sku_base: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    ncm: Mapped[str | None] = mapped_column(String(8))
    regulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    regulatory_docs: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    images: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    # Mídia com texto alternativo [{url, alt}] (COM-001). `images` = URLs na mesma ordem (o que os canais recebem).
    media: Mapped[list[Any]] = mapped_column(nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class MediaBlob(Base):
    """Foto de produto reprocessada (JPEG sem metadados), endereçada pelo SHA-256. Entra nos backups do banco."""

    __tablename__ = "media_blobs"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class DesktopRelease(Base):
    """Versão do app desktop publicada no servidor (atualização automática). Revogar tira a versão do ar."""

    __tablename__ = "desktop_releases"
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_by: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[dt.datetime] = _created()
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class DesktopArtifact(Base):
    """Pacote de atualização de uma plataforma (ex.: darwin-aarch64) + assinatura minisign do Tauri.
    O app só instala se a assinatura bater com a chave pública embutida nele."""

    __tablename__ = "desktop_artifacts"
    __table_args__ = (UniqueConstraint("version", "platform"),)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(ForeignKey("desktop_releases.version", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductVersion(Base):
    """Snapshot imutável de cada versão do produto (quem mudou, o quê). Append-only (trigger)."""

    __tablename__ = "product_versions"
    __table_args__ = (UniqueConstraint("product_id", "version"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    changed_fields: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        CheckConstraint("weight_g > 0", name="ck_variant_weight_pos"),
        Index("ix_variants_product", "product_id"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    gtin: Mapped[str | None] = mapped_column(String(14))
    weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[dt.datetime] = _created()


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = _pk_uuid()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_id: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROSPECT")
    sla_dispatch_days: Mapped[int | None] = mapped_column(Integer)
    sample_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_REQUESTED")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class SupplierDocument(Base):
    __tablename__ = "supplier_documents"
    id: Mapped[uuid.UUID] = _pk_uuid()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_by: Mapped[str | None] = mapped_column(String(128))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class SupplierOffer(Base):
    __tablename__ = "supplier_offers"
    __table_args__ = (
        CheckConstraint("cost_minor > 0", name="ck_offer_cost_pos"),
        CheckConstraint("stock_qty >= 0", name="ck_offer_stock_nonneg"),
        Index("ix_offers_variant_status", "variant_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id"), nullable=False)
    cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    moq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class ChannelFeeSchedule(Base):
    """Tarifas parametrizadas por canal e versão. Valores semeados são FICTÍCIOS até verificação."""

    __tablename__ = "channel_fee_schedules"
    __table_args__ = (UniqueConstraint("channel", "version"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_fee_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    fixed_fee_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_note: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = _created()


class TaxProfile(Base):
    """Alíquota efetiva parametrizada. Só vale como cálculo real com approved_by_accountant (SDD §10)."""

    __tablename__ = "tax_profiles"
    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    effective_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_by_accountant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = _created()


class ChannelListing(Base):
    __tablename__ = "channel_listings"
    __table_args__ = (UniqueConstraint("variant_id", "channel"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id"), nullable=False)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier_offers.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    fulfillment_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    channel_payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    pricing: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    compliance: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    external_id: Mapped[str | None] = mapped_column(String(128))
    # Última modificação vista no canal (webhook): eventos mais antigos que isto são ignorados (fora de ordem).
    external_modified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_proposals.id"))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class Campaign(Base):
    """Campanha de mídia criada por CREATE_CAMPAIGN executada (COM-008). O teto é a reserva aprovada no ledger."""

    __tablename__ = "campaigns"
    id: Mapped[uuid.UUID] = _pk_uuid()
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_proposals.id"), nullable=False, unique=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128))
    # ACTIVE → PAUSE_REQUESTED → PAUSED; ENDED quando o teto é consumido; ERROR se a pausa falhar
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    daily_budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cap_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    budget_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("money_accounts.id"))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("budget_reservations.id"))
    pause_proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_proposals.id"))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class CampaignMetric(Base):
    """Métrica diária importada da plataforma de mídia. O gasto vira lançamento SPEND no ledger (idempotente)."""

    __tablename__ = "campaign_metrics"
    __table_args__ = (UniqueConstraint("campaign_id", "day"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    clicks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False)
    revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spend_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    ledger_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = _created()


class ListingVersion(Base):
    """Snapshot imutável de cada versão preparada (o que foi proposto/aprovado)."""

    __tablename__ = "listing_versions"
    __table_args__ = (UniqueConstraint("listing_id", "version"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_listings.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pricing: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class WebhookEvent(Base):
    """Webhooks recebidos: persistidos antes do ACK, deduplicados por (provider, delivery_id)."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "delivery_id"), Index("ix_webhooks_status", "status"))
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RECEIVED")
    result: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[dt.datetime] = _created()
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# ───────────────────────────── Pedidos, pagamentos, logística, fiscal, conciliação ─────────────────────────────
class Order(Base):
    """Pedido de canal (SDD §9). Identidade externa única por (canal, external_id)."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("channel", "external_id"),
        Index("ix_orders_status_placed", "status", "placed_at"),
        CheckConstraint("total_minor >= 0", name="ck_order_total_nonneg"),
    )
    id: Mapped[uuid.UUID] = _pk_uuid()
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    fiscal_status: Mapped[str] = mapped_column(String(24), nullable=False, default="NOT_STARTED")
    items_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipping_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    discount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    uf: Mapped[str | None] = mapped_column(String(2))
    # PII cifrada (AES-GCM) — leitura exige orders:pii e é auditada (LGPD: minimização + acesso restrito).
    customer_ciphertext: Mapped[str | None] = mapped_column(Text)
    customer_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # pseudônimo estável (hash)
    placed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    external_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    incident_reason: Mapped[str | None] = mapped_column(Text)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("qty > 0", name="ck_item_qty_pos"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_listings.id"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variants.id"))
    offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier_offers.id"))
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    stock_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OrderEvent(Base):
    """Histórico append-only de transições do pedido."""

    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order", "order_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    occurred_at: Mapped[dt.datetime] = _created()


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    captured_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class Fulfillment(Base):
    __tablename__ = "fulfillments"
    id: Mapped[uuid.UUID] = _pk_uuid()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    supplier_order_ref: Mapped[str | None] = mapped_column(String(128))
    carrier: Mapped[str | None] = mapped_column(String(64))
    tracking_code: Mapped[str | None] = mapped_column(String(64))
    cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    promised_by: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shipped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_proposals.id"))
    created_at: Mapped[dt.datetime] = _created()


class ReturnRequest(Base):
    __tablename__ = "returns"
    id: Mapped[uuid.UUID] = _pk_uuid()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    withdrawal_right: Mapped[bool] = mapped_column(Boolean, nullable=False)  # CDC art. 49 (7 dias)
    refund_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_proposals.id"))
    requested_at: Mapped[dt.datetime] = _created()


class FiscalDocument(Base):
    """Documento fiscal. Em DRY_RUN é sempre FICTÍCIO (sem valor fiscal); LIVE sem modelo aprovado = FISCAL_HOLD."""

    __tablename__ = "fiscal_documents"
    __table_args__ = (UniqueConstraint("order_id", "kind"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    access_key: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()


class Settlement(Base):
    """Repasse do canal (extrato). Linhas casadas com pedidos na conciliação."""

    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fees_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    imported_at: Mapped[dt.datetime] = _created()


class SettlementLine(Base):
    __tablename__ = "settlement_lines"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("settlements.id"), nullable=False)
    order_external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="SALE"
    )  # SALE|REFUND|CHARGEBACK|CHARGEBACK_REVERSAL


# ───────────────────────────── IA: custo, cache, workflows, oportunidades, conhecimento ─────────────────────────────
EMBEDDING_DIM = 256


class AIUsage(Base):
    """Ledger append-only de custo de IA por agente/tarefa/modelo (FR-017; SDD §12)."""

    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_created", "created_at"), Index("ix_ai_usage_task", "task_id"))
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    route_reason: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    request_cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    budget_remaining_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stop_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = _created()


class AISpendHold(Base):
    """Reserva do pior custo de uma chamada de IA antes de chamar (teto mensal atômico; auditoria A02)."""

    __tablename__ = "ai_spend_holds"
    __table_args__ = (Index("ix_ai_spend_holds_expires", "expires_at"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # atribuição: se o processo cair durante a chamada, a reserva expirada vira custo desta tarefa (A02/A07)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class LLMCache(Base):
    """Cache de respostas por hash de conteúdo com TTL (deduplicação; SDD §12)."""

    __tablename__ = "llm_cache"
    key_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    output: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class WorkflowCheckpoint(Base):
    """Checkpoint por nó crítico de workflow (FR-003). Append-only: a retomada lê o último."""

    __tablename__ = "workflow_checkpoints"
    __table_args__ = (Index("ix_checkpoints_task", "task_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_tasks.id"), nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class Opportunity(Base):
    """Dossiê de oportunidade com fontes, data e qualidade de evidência (FR-004)."""

    __tablename__ = "opportunities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_tasks.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    contraindications: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    evidence_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    sources: Mapped[list[Any]] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="NEW")
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class KBDocument(Base):
    """Documento do Knowledge Hub com os metadados do SDD §11."""

    __tablename__ = "kb_documents"
    __table_args__ = (UniqueConstraint("collection", "content_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(String(200), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(16))
    channel: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="pt-BR")
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False)
    access_policy: Mapped[list[Any]] = mapped_column(nullable=False)  # papéis/agentes com acesso
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    suspicious: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class KBChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index("ix_kb_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_kb_chunks_doc", "document_id"),
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('portuguese', text)", persisted=True))
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    embedder: Mapped[str] = mapped_column(
        String(64), nullable=False, default="hashing-v1", server_default="hashing-v1", index=True
    )


class RetrievalAudit(Base):
    """Registro de cada consulta ao conhecimento: quem, o quê, quais trechos voltaram (SDD §11)."""

    __tablename__ = "retrieval_audits"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    requester: Mapped[str] = mapped_column(String(128), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(String(16), nullable=False)
    returned_chunk_ids: Mapped[list[Any]] = mapped_column(nullable=False)
    excluded: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = _created()


class AgentChatMessage(Base):
    __tablename__ = "agent_chat_messages"
    __table_args__ = (Index("ix_chat_conv", "conversation_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = _created()


# ───────────────────────────── Contas de canal, categorias e recursos simulados ─────────────────────────────
class ChannelAccount(Base):
    """Conta conectada a um canal. Tokens OAuth cifrados (AES-GCM); identidade externa única por canal."""

    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("channel", "external_account_id"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CONNECTED")
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class ChannelCategoryMap(Base):
    """Categoria interna → categoria do canal (marketplaces exigem categoria-folha própria)."""

    __tablename__ = "channel_category_map"
    __table_args__ = (UniqueConstraint("channel", "internal_category"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    internal_category: Mapped[str] = mapped_column(String(120), nullable=False)
    external_category_id: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class OAuthState(Base):
    """Fluxo OAuth em andamento (PKCE). O verificador fica cifrado no servidor; o state (só o hash) é de uso único
    e expira em 10 min — o navegador do proprietário só carrega o state, nunca o verificador."""

    __tablename__ = "oauth_states"
    state_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    code_verifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SimChannelResource(Base):
    """Recursos que um canal SIMULADO expõe para busca após notificação (ex.: /orders/123 no ML)."""

    __tablename__ = "sim_channel_resources"
    __table_args__ = (UniqueConstraint("channel", "resource"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class GateEvidence(Base):
    """Evidência de um gate de certificação (SDD §3, §15). Append-only; o hash vai para a aprovação."""

    __tablename__ = "gate_evidence"
    __table_args__ = (Index("ix_gate_evidence_gate", "gate_code"),)
    id: Mapped[uuid.UUID] = _pk_uuid()
    gate_code: Mapped[str] = mapped_column(ForeignKey("certification_gates.code"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[dt.datetime] = _created()
