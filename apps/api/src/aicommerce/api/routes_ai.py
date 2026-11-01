"""Missões, oportunidades, chat, Knowledge Hub e custo de IA (AI-001..010, OFF-004; SDD §14)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.ai import chat as chat_mod
from aicommerce.ai import knowledge, llm
from aicommerce.ai.specs import SPECS
from aicommerce.api.deps import request_id, require
from aicommerce.api.routes_governance import proposal_out
from aicommerce.config import get_settings
from aicommerce.db import get_db
from aicommerce.identity.security import Principal
from aicommerce.models import AgentTask, AIUsage, KBDocument, Opportunity
from aicommerce.workforce import tasks

router = APIRouter(prefix="/v1", tags=["ai"])


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


@router.get("/research/sources")
def research_sources(_: Principal = Depends(require("office:read"))):
    """Inventário das fontes de pesquisa: plano por agente + estado real de cada provedor externo (mock /
    unavailable / configured / verified), tetos e uso no período. Somente leitura; não faz chamada externa."""
    from aicommerce.ai.research_guard import inventory
    from aicommerce.ai.source_catalog import SOURCE_CATALOG

    return {"providers": inventory(), "by_agent": SOURCE_CATALOG}


@router.get("/ai/specs")
def list_specs(_: Principal = Depends(require("office:read"))):
    return {
        "items": [
            {"kind": s.kind, "agent_id": s.agent_id, "title": s.title, "tier": s.tier}
            for s in SPECS.values()
            if s.kind != "mission.summarize"
        ],
        "provider": get_settings().llm_provider,
        "models": {"small": llm.route(spec_tier="small").model, "advanced": llm.route(spec_tier="advanced").model},
    }


class MissionIn(BaseModel):
    goal: str = Field(min_length=5, max_length=500)
    max_steps: int = Field(
        default=3, ge=1, le=6
    )  # limite de fan-out do Atlas (SDD §7); orçamento por execução vale igual


@router.post("/missions", status_code=status.HTTP_201_CREATED)
def create_mission(body: MissionIn, db: Session = Depends(get_db), p: Principal = Depends(require("ai:missions"))):
    t = tasks.create(db, agent_id="atlas", kind="mission.plan", input_=body.model_dump(), actor=p.user_id)
    return {"mission_id": str(t.id), "state": t.state}


# Classificação da espera/bloqueio em linguagem de operação (SDD v3 UX-04). Derivada só de estado real.
POLICY_KINDS = {"LIMIT_REACHED", "FORBIDDEN", "TOOL_FORBIDDEN", "POLICY", "UNSUPPORTED", "REFUSAL"}
RECOVERABLE_KINDS = {"TRANSIENT", "RUNTIME", "OUTPUT_INVALID", "TRUNCATED", "LLM_ERROR", "LEASE_EXPIRED", "EXECUTOR"}
OPEN_STATES = ("CREATED", "QUEUED", "RUNNING", "WAITING_EXTERNAL", "WAITING_APPROVAL", "RESUMED")
TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED")


def _error_kind(error: str | None) -> str | None:
    if not error:
        return None
    head = error.split(":", 1)[0].strip()
    return head if head.isupper() and " " not in head else "FAILED"


def _blocker(state: str, error: str | None, child_states: list[str]) -> str | None:
    if state == "WAITING_APPROVAL" or "WAITING_APPROVAL" in child_states:
        return "WAITING_APPROVAL"
    if state == "WAITING_EXTERNAL":
        return "WAITING_TOOL"
    if state == "FAILED":
        kind = _error_kind(error)
        if kind in POLICY_KINDS:
            return "BLOCKED_POLICY"
        if kind in RECOVERABLE_KINDS:
            return "RECOVERABLE"
        return "FAILED"
    return None


def _mission_cost(db: Session, ids: list[uuid.UUID]) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(AIUsage.request_cost_micro_usd), 0)).where(
                AIUsage.mission_id.in_(ids) | AIUsage.task_id.in_(ids)
            )
        ).scalar_one()
    )


def _mission(db: Session, mission_id: uuid.UUID) -> AgentTask:
    m = db.get(AgentTask, mission_id)
    if m is None or m.kind != "mission.plan":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "missão inexistente")
    return m


def _mission_summary(db: Session, m: AgentTask, kids: list[AgentTask]) -> dict[str, Any]:
    states = [c.state for c in kids]
    last = max([m.updated_at, *[c.updated_at for c in kids]])
    end = last if m.state in TERMINAL_STATES else dt.datetime.now(dt.UTC)
    return {
        "id": str(m.id),
        "state": m.state,
        "goal": m.input.get("goal"),
        "max_steps": m.input.get("max_steps"),
        "rerun_of": m.input.get("rerun_of"),
        "simulated": m.mode == "DRY_RUN",
        "error": m.error,
        "error_kind": _error_kind(m.error),
        "blocker": _blocker(m.state, m.error, states),
        "agents": sorted({"atlas", *[c.agent_id for c in kids]}),
        "steps_total": len(kids),
        "steps_done": sum(s == "COMPLETED" for s in states),
        "steps_failed": sum(s == "FAILED" for s in states),
        "cost_micro_usd": _mission_cost(db, [m.id, *[c.id for c in kids]]),
        "created_at": _iso(m.created_at),
        "updated_at": _iso(last),
        "duration_s": max(0, int((end - m.created_at).total_seconds())),
    }


def _children(db: Session, mission_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[AgentTask]]:
    out: dict[uuid.UUID, list[AgentTask]] = {i: [] for i in mission_ids}
    if mission_ids:
        for c in db.execute(
            select(AgentTask).where(AgentTask.parent_task_id.in_(mission_ids)).order_by(AgentTask.created_at)
        ).scalars():
            out[c.parent_task_id].append(c)
    return out


@router.get("/missions")
def list_missions(
    state: str | None = Query(default=None, description="OPEN, DONE ou um estado exato"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("tasks:read")),
):
    q = select(AgentTask).where(AgentTask.kind == "mission.plan").order_by(AgentTask.created_at.desc()).limit(limit)
    if state == "OPEN":
        q = q.where(AgentTask.state.in_(OPEN_STATES))
    elif state == "DONE":
        q = q.where(AgentTask.state.in_(TERMINAL_STATES))
    elif state:
        q = q.where(AgentTask.state == state)
    ms = list(db.execute(q).scalars())
    kids = _children(db, [m.id for m in ms])
    return {"items": [_mission_summary(db, m, kids[m.id]) for m in ms]}


@router.get("/missions/{mission_id}")
def get_mission(mission_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("tasks:read"))):
    from aicommerce.models import OfficeEvent, WorkflowCheckpoint

    m = _mission(db, mission_id)
    kids = _children(db, [m.id])[m.id]
    ids = [m.id, *[c.id for c in kids]]
    # Linha do tempo = eventos reais de estado das tarefas da missão + checkpoints do workflow (sem texto inventado)
    events = db.execute(select(OfficeEvent).where(OfficeEvent.task_id.in_(ids)).order_by(OfficeEvent.seq)).scalars()
    timeline = [
        {
            "at": _iso(e.occurred_at),
            "seq": e.seq,
            "source": "event",
            "task_id": str(e.task_id),
            "agent_id": e.agent_id,
            "type": e.type,
            "task_state": (e.payload or {}).get("task_state"),
            "from": (e.payload or {}).get("from"),
            "tool": (e.payload or {}).get("tool"),
            "detail": {k: v for k, v in (e.payload or {}).items() if k in ("error_kind", "children", "node", "reason")},
        }
        for e in events
    ]
    checkpoints = [
        {"at": _iso(c.created_at), "task_id": str(c.task_id), "node": c.node}
        for c in db.execute(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.task_id.in_(ids)).order_by(WorkflowCheckpoint.id)
        ).scalars()
    ]
    plan = {p.get("kind"): p.get("why") for p in ((m.result or {}).get("plan") or []) if isinstance(p, dict)}
    return {
        **_mission_summary(db, m, kids),
        "result": m.result,
        "children": [
            {
                "id": str(c.id),
                "agent_id": c.agent_id,
                "kind": c.kind,
                "state": c.state,
                "why": plan.get(c.kind),
                "summary": (c.result or {}).get("summary"),
                "error": c.error,
                "error_kind": _error_kind(c.error),
                "attempts": c.attempts,
                "cost_micro_usd": _mission_cost(db, [c.id]),
                "created_at": _iso(c.created_at),
                "updated_at": _iso(c.updated_at),
            }
            for c in kids
        ],
        "timeline": timeline,
        "checkpoints": checkpoints,
    }


class MissionCancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(
    mission_id: uuid.UUID,
    body: MissionCancelIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("ai:missions")),
):
    """Cancela a missão e as subtarefas ainda abertas. Idempotente: missão já encerrada → 409 com o estado."""
    from aicommerce.governance import audit

    m = db.get(AgentTask, mission_id, with_for_update=True)
    if m is None or m.kind != "mission.plan":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "missão inexistente")
    if m.state in TERMINAL_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "INVALID_STATE", "message": f"missão já encerrada ({m.state})"}
        )
    kids = list(db.execute(select(AgentTask).where(AgentTask.parent_task_id == m.id).with_for_update()).scalars())
    cancelled = []
    for c in kids:
        if c.state not in TERMINAL_STATES:
            tasks.transition(db, c, tasks.TaskState.CANCELLED, detail={"reason": "mission_cancelled"})
            cancelled.append(str(c.id))
    tasks.transition(
        db, m, tasks.TaskState.CANCELLED, error=f"CANCELLED: {body.reason}", detail={"reason": body.reason}
    )
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action="mission.cancelled",
        subject_type="agent_task",
        subject_id=str(m.id),
        payload={"reason": body.reason, "children_cancelled": cancelled},
    )
    db.flush()
    return get_mission(mission_id, db, p)


@router.post("/missions/{mission_id}/rerun", status_code=status.HTTP_201_CREATED)
def rerun_mission(mission_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("ai:missions"))):
    """Reexecuta como NOVA missão (mesmo objetivo); a original e o histórico ficam intactos."""
    m = _mission(db, mission_id)
    if m.state not in TERMINAL_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "INVALID_STATE", "message": "a missão ainda está em andamento; cancele antes de reexecutar"},
        )
    inp = {"goal": m.input.get("goal"), "max_steps": m.input.get("max_steps", 3), "rerun_of": str(m.id)}
    t = tasks.create(db, agent_id="atlas", kind="mission.plan", input_=inp, actor=p.user_id)
    return {"mission_id": str(t.id), "state": t.state, "rerun_of": str(m.id)}


@router.get("/opportunities")
def list_opportunities(db: Session = Depends(get_db), _: Principal = Depends(require("catalog:read"))):
    rows = db.execute(select(Opportunity).order_by(Opportunity.created_at.desc()).limit(100)).scalars()
    return {
        "items": [
            {
                "id": str(o.id),
                "title": o.title,
                "category": o.category,
                "rationale": o.rationale,
                "score": o.score,
                "evidence_quality": o.evidence_quality,
                "sources": o.sources,
                "contraindications": o.contraindications,
                "status": o.status,
                "simulated": o.is_simulated,
                "created_at": _iso(o.created_at),
            }
            for o in rows
        ]
    }


@router.post("/opportunities/{opportunity_id}/proposals", status_code=status.HTTP_201_CREATED)
def opportunity_proposal(
    opportunity_id: uuid.UUID,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("proposals:create")),
    rid: str = Depends(request_id),
):
    """Transforma a oportunidade em proposta de rascunho de anúncio (sem efeito externo; passa pela política)."""
    from aicommerce.governance.service import submit_proposal

    o = db.get(Opportunity, opportunity_id, with_for_update=True)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "oportunidade inexistente")
    data = {
        "action_type": "PREPARE_LISTING_DRAFT",
        "agent_id": "luna",
        "subject_type": "opportunity",
        "subject_id": str(o.id),
        "subject_version": 1,
        "payload": {"title": o.title, "category": o.category, "sources": o.sources},
        "risk": "LOW",
        "evidence_refs": [f"opportunity:{o.id}"] + [f"source:{s['id']}" for s in o.sources],
        "monetary_scope_minor": 0,
        "currency": "BRL",
        "channel_account_id": None,
        "idempotency_key": f"opportunity:{o.id}:draft",
    }
    prop, _ = submit_proposal(db, data, actor=str(p.user_id), request_id=rid, golive_req=None)
    o.status = "PROPOSED"
    return proposal_out(prop)


class PlatformPublishIn(BaseModel):
    price_minor: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=60)
    platform: str = "DROPI"
    channel: str = "MERCADO_LIVRE"
    supplier_freight_minor: int | None = Field(default=None, ge=0)


@router.post("/opportunities/{opportunity_id}/platform-publication", status_code=status.HTTP_201_CREATED)
def opportunity_platform_publication(
    opportunity_id: uuid.UUID,
    body: PlatformPublishIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("proposals:create")),
    rid: str = Depends(request_id),
):
    """Proposta de publicar pela plataforma parceira (Dropi) — exige aprovação humana e homologações em LIVE."""
    from aicommerce.commerce import platform_publish

    try:
        prop = platform_publish.propose(
            db,
            opportunity_id,
            actor=str(p.user_id),
            price_minor=body.price_minor,
            title=body.title,
            platform=body.platform,
            channel=body.channel,
            supplier_freight_minor=body.supplier_freight_minor,
            request_id=rid,
        )
    except platform_publish.PlatformPublishError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return proposal_out(prop)


class ChatIn(BaseModel):
    message: str = Field(min_length=2, max_length=4000)
    conversation_id: uuid.UUID | None = None


@router.post("/agents/{agent_id}/chat")
def agent_chat(agent_id: str, body: ChatIn, db: Session = Depends(get_db), p: Principal = Depends(require("ai:use"))):
    db.commit()  # libera a conexão: a chamada ao LLM não deve segurar transação aberta
    try:
        return chat_mod.chat(
            agent_id=agent_id,
            user_id=p.user_id,
            roles=set(p.roles),
            message=body.message,
            conversation_id=body.conversation_id,
        )
    except chat_mod.ChatError as e:
        raise HTTPException(e.http, str(e)) from e
    except llm.LimitReached as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, {"code": "LIMIT_REACHED", "message": str(e)}) from e
    except llm.LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"code": e.kind, "message": str(e)}) from e


@router.get("/conversations")
def list_conversations(
    agent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    p: Principal = Depends(require("ai:use")),
):
    """Conversas do PRÓPRIO usuário (nunca de outra pessoa), mais recentes primeiro. Uma conversa pode ter
    passado por mais de um agente; `agent_id` é quem abriu, `agents` todos que responderam."""
    from aicommerce.models import AgentChatMessage as M

    q = (
        select(
            M.conversation_id,
            func.array_agg(func.distinct(M.agent_id)),
            func.min(M.created_at),
            func.max(M.created_at),
            func.count(),
            func.min(M.id),
        )
        .where(M.user_id == p.user_id)
        .group_by(M.conversation_id)
        .order_by(func.max(M.created_at).desc())
        .limit(limit)
    )
    if agent_id:
        q = q.having(func.bool_or(M.agent_id == agent_id))
    rows = db.execute(q).all()
    if not rows:
        return {"items": []}
    first = {m.conversation_id: m for m in db.execute(select(M).where(M.id.in_([r[5] for r in rows]))).scalars()}
    costs = dict(
        db.execute(
            select(M.conversation_id, func.coalesce(func.sum(M.meta["cost_micro_usd"].as_integer()), 0))
            .where(M.conversation_id.in_([r[0] for r in rows]), M.role == "assistant")
            .group_by(M.conversation_id)
        ).all()
    )
    return {
        "items": [
            {
                "id": str(r[0]),
                "agent_id": first[r[0]].agent_id,
                "agents": sorted(r[1]),
                "title": first[r[0]].content[:80],
                "started_at": _iso(r[2]),
                "last_at": _iso(r[3]),
                "messages": r[4],
                "cost_micro_usd": int(costs.get(r[0], 0) or 0),
            }
            for r in rows
        ]
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    p: Principal = Depends(require("ai:use")),
):
    """Histórico paginado (do mais novo para trás). Só o dono enxerga; não expõe raciocínio interno, só o texto."""
    from aicommerce.models import AgentChatMessage as M

    q = select(M).where(M.conversation_id == conversation_id, M.user_id == p.user_id).order_by(M.id.desc()).limit(limit)
    if before_id:
        q = q.where(M.id < before_id)
    rows = list(db.execute(q).scalars())
    if not rows and before_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversa inexistente")
    keep = ("route", "model", "tier", "cost_micro_usd", "source_ids")
    return {
        "id": str(conversation_id),
        "agent_id": rows[0].agent_id if rows else None,
        "items": [
            {
                "id": m.id,
                "agent_id": m.agent_id,
                "role": m.role,
                "content": m.content,
                "meta": {k: v for k, v in (m.meta or {}).items() if k in keep},
                "at": _iso(m.created_at),
            }
            for m in reversed(rows)
        ],
        "next_before_id": rows[-1].id if len(rows) == limit else None,
    }


# ───────────── Knowledge Hub ─────────────
class DocIn(BaseModel):
    collection: str
    title: str = Field(min_length=3, max_length=300)
    source_url: str = Field(min_length=5, max_length=1000)
    publisher: str = Field(min_length=2, max_length=200)
    document_type: str = Field(min_length=2, max_length=32)
    access_policy: list[str] = Field(min_length=1)
    content: str = Field(min_length=40, max_length=500_000)
    jurisdiction: str | None = "BR"
    channel: str | None = None
    published_at: dt.datetime | None = None
    valid_until: dt.datetime | None = None


def doc_out(d: KBDocument) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "collection": d.collection,
        "title": d.title,
        "source_url": d.source_url,
        "publisher": d.publisher,
        "document_type": d.document_type,
        "version": d.version,
        "approval_status": d.approval_status,
        "suspicious": d.suspicious,
        "access_policy": d.access_policy,
        "valid_until": _iso(d.valid_until),
        "verified_at": _iso(d.verified_at),
        "published_at": _iso(d.published_at),
        "created_at": _iso(d.created_at),
        "expired": bool(d.valid_until and d.valid_until < dt.datetime.now(dt.UTC)),
        "owner": d.owner,
        "jurisdiction": d.jurisdiction,
        "channel": d.channel,
        "simulated": d.is_simulated,
    }


@router.post("/knowledge/documents", status_code=status.HTTP_201_CREATED)
def ingest_document(body: DocIn, db: Session = Depends(get_db), p: Principal = Depends(require("kb:write"))):
    meta = knowledge.DocMeta(
        collection=body.collection,
        title=body.title,
        source_url=body.source_url,
        publisher=body.publisher,
        document_type=body.document_type,
        owner=str(p.user_id),
        access_policy=body.access_policy,
        jurisdiction=body.jurisdiction,
        channel=body.channel,
        published_at=body.published_at,
        valid_until=body.valid_until,
    )
    try:
        doc, created = knowledge.ingest(db, meta, body.content, actor=str(p.user_id))
    except knowledge.KnowledgeUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except knowledge.KnowledgeError as e:
        raise HTTPException(422, str(e)) from e
    return {**doc_out(doc), "replayed": not created}


@router.get("/knowledge/documents")
def list_documents(
    collection: str | None = None,
    approval_status: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    p: Principal = Depends(require("kb:read")),
):
    """Metadados dos documentos que o usuário pode ler (mesma ACL da busca; revisores veem todos)."""
    from aicommerce.models import User

    stmt = select(KBDocument).order_by(KBDocument.created_at.desc()).limit(200)
    if collection:
        stmt = stmt.where(KBDocument.collection == collection)
    if approval_status:
        stmt = stmt.where(KBDocument.approval_status == approval_status)
    if q:
        stmt = stmt.where(KBDocument.title.ilike(f"%{q}%") | KBDocument.publisher.ilike(f"%{q}%"))
    reviewer = "kb:approve" in p.permissions
    principals = set(p.roles) | {"all"}
    rows = [d for d in db.execute(stmt).scalars() if reviewer or knowledge._allowed(d, principals)]
    ids = {uuid.UUID(d.owner) for d in rows if _is_uuid(d.owner)}
    emails = {k: v for k, v in db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()} if ids else {}
    collections = sorted(set(db.execute(select(KBDocument.collection).distinct()).scalars()))
    return {
        "items": [
            {**doc_out(d), "owner_email": emails.get(uuid.UUID(d.owner)) if _is_uuid(d.owner) else None} for d in rows
        ],
        "collections": collections,
    }


def _is_uuid(v: str) -> bool:
    try:
        uuid.UUID(v)
    except (ValueError, TypeError):
        return False
    return True


class ReviewIn(BaseModel):
    approved: bool
    note: str = Field(min_length=3, max_length=1000)


@router.post("/knowledge/documents/{doc_id}/review")
def review_document(
    doc_id: uuid.UUID, body: ReviewIn, db: Session = Depends(get_db), p: Principal = Depends(require("kb:approve"))
):
    try:
        return doc_out(knowledge.approve(db, doc_id, actor=str(p.user_id), approved=body.approved, note=body.note))
    except knowledge.KnowledgeError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/knowledge/search")
def kb_search(
    q: str = Query(min_length=2, max_length=500),
    collection: str | None = None,
    k: int = Query(5, le=20),
    db: Session = Depends(get_db),
    p: Principal = Depends(require("kb:read")),
):
    hits = knowledge.search(
        db,
        q,
        requester=f"user:{p.user_id}",
        principals=set(p.roles) | {"all"},
        collections={collection} if collection else None,
        k=k,
    )
    return {
        "route": knowledge.route_query(q),
        "items": [
            {
                "document_id": h.document_id,
                "title": h.title,
                "collection": h.collection,
                "url": h.source_url,
                "approval_status": h.approval_status,
                "score": h.score,
                "text": h.text,
            }
            for h in hits
        ],
    }


@router.get("/ai/usage")
def ai_usage(db: Session = Depends(get_db), _: Principal = Depends(require("finance:read"))):
    rows = db.execute(
        select(
            AIUsage.agent_id,
            AIUsage.model,
            func.count(),
            func.sum(AIUsage.input_tokens),
            func.sum(AIUsage.cached_tokens),
            func.sum(AIUsage.output_tokens),
            func.sum(AIUsage.request_cost_micro_usd),
            func.bool_or(AIUsage.simulated),
        )
        # mesmo período do total: mês corrente (auditoria A05)
        .where(AIUsage.created_at >= dt.datetime.now(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        .group_by(AIUsage.agent_id, AIUsage.model)
    ).all()
    return {
        "month_spend_micro_usd": llm.month_spend_micro_usd(db),
        "monthly_budget_micro_usd": get_settings().ai_monthly_budget_micro_usd,
        "provider": get_settings().llm_provider,
        "by_agent_model": [
            {
                "agent_id": a,
                "model": m,
                "calls": n,
                "input_tokens": int(i or 0),
                "cached_tokens": int(c or 0),
                "output_tokens": int(o or 0),
                "cost_micro_usd": int(cost or 0),
                "simulated": bool(sim),
            }
            for a, m, n, i, c, o, cost, sim in rows
        ],
    }
