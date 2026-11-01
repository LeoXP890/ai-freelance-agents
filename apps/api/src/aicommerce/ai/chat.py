"""Chat contextual com agentes (OFF-004). Somente leitura: nunca executa ação.

Roteador de consulta (SDD §11): SQL (números do sistema), regra formal (política/gates) ou documentos (KB).
A mensagem do usuário e os trechos do KB entram como dados; a resposta é JSON validado (ids citados ⊆ fornecidos).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.ai import knowledge, llm
from aicommerce.ai.runtime import _config, system_prompt
from aicommerce.ai.specs import AGENT_SCOPES, _facts_finance, _facts_logistics, _facts_sales, arr, enum, obj, prompt
from aicommerce.db import session_scope
from aicommerce.governance import killswitch
from aicommerce.governance.service import active_policy
from aicommerce.models import Agent, AgentChatMessage, CertificationGate

SCHEMA = obj(
    {
        "answer": {"type": "string"},
        "source_ids": arr({"type": "string"}),
        "fact_ids": arr({"type": "string"}),
        "suggested_action": obj(
            {
                "action_type": enum(
                    "NONE",
                    "PUBLISH_LISTING",
                    "CREATE_CAMPAIGN",
                    "SUPPLIER_PURCHASE",
                    "REFUND",
                    "PAUSE_LISTING",
                    "RESEARCH",
                ),
                "description": {"type": "string"},
            }
        ),
    }
)
HISTORY = 6


class ChatError(Exception):
    http = 422


class ConversationNotFound(ChatError):
    http = 404


def _facts(db: Session, agent_id: str, route: str) -> dict[str, Any]:
    if route == "rule":
        pv = active_policy(db)
        return {
            "F_politica_ativa": pv.version,
            "F_acoes_com_aprovacao": pv.rules["require_approval_actions"],
            "F_kill_switch_ativo": killswitch.is_active(db),
            "F_gates": {g.code: g.state for g in db.execute(select(CertificationGate)).scalars()},
        }
    if route == "sql":
        if agent_id in ("finn", "atlas"):
            return _facts_finance(db) | _facts_sales(db)
        if agent_id == "logan":
            return _facts_logistics(db)
        return _facts_sales(db)
    return {}


def _mock(route: str, facts: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    if route == "retrieval" and sources:
        return {
            "answer": f"Segundo a fonte {sources[0]['id']} ('{sources[0]['title']}'), há informação relacionada. "
            "(Resposta gerada pelo mock, sem LLM real.)",
            "source_ids": [sources[0]["id"]],
            "fact_ids": [],
            "suggested_action": {"action_type": "NONE", "description": ""},
        }
    keys = [k for k in facts if k.startswith("F_")][:2]
    if keys:
        return {
            "answer": "Pelos dados do sistema: "
            + "; ".join(f"{k} = {json.dumps(facts[k], ensure_ascii=False)}" for k in keys)
            + " (mock).",
            "source_ids": [],
            "fact_ids": keys,
            "suggested_action": {"action_type": "NONE", "description": ""},
        }
    return {
        "answer": "Não encontrei dados ou fontes suficientes para responder com segurança. (mock)",
        "source_ids": [],
        "fact_ids": [],
        "suggested_action": {"action_type": "NONE", "description": ""},
    }


def chat(
    *, agent_id: str, user_id: uuid.UUID, roles: set[str], message: str, conversation_id: uuid.UUID | None
) -> dict[str, Any]:
    message = message.strip()
    if not 2 <= len(message) <= 4000:
        raise ChatError("mensagem deve ter entre 2 e 4000 caracteres")
    conv = conversation_id or uuid.uuid4()
    route = knowledge.route_query(message)
    with session_scope() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            raise ChatError("agente inexistente")
        cfg = _config(db, agent_id)
        facts = _facts(db, agent_id, route)
        hits = (
            knowledge.search(
                db,
                message,
                requester=f"user:{user_id}",
                principals=roles | {"all"},
                collections=AGENT_SCOPES[agent_id],
                k=3,
            )
            if route == "retrieval"
            else []
        )
        sources = knowledge.hits_as_sources(hits)
        if conversation_id is not None:
            # a conversa é do usuário: id de outra pessoa não injeta histórico alheio no prompt (pode seguir com
            # outro agente — conversa multiagente do mesmo usuário é permitida)
            owner = db.execute(
                select(AgentChatMessage.user_id).where(AgentChatMessage.conversation_id == conv).limit(1)
            ).scalar_one_or_none()
            if owner is not None and owner != user_id:
                raise ConversationNotFound("conversa inexistente")
        history = (
            db.execute(
                select(AgentChatMessage)
                .where(AgentChatMessage.conversation_id == conv, AgentChatMessage.user_id == user_id)
                .order_by(AgentChatMessage.id.desc())
                .limit(HISTORY)
            )
            .scalars()
            .all()[::-1]
        )
        hist = "\n".join(f"{m.role}: {m.content[:500]}" for m in history)
        facts["_ids"] = [k for k in facts if k.startswith("F_")]
        user_prompt = prompt(
            f"Responder ao usuário (rota de consulta: {route}). Você só informa e sugere; ações reais "
            "exigem proposta e aprovação humana.",
            facts,
            [{"id": "U", "title": "Pergunta do usuário", "text": message}, *sources],
            extra=f"HISTÓRICO RECENTE:\n{hist}" if hist else "",
        )
        system = system_prompt(agent, cfg)
        db.add(
            AgentChatMessage(
                conversation_id=conv,
                agent_id=agent_id,
                user_id=user_id,
                role="user",
                content=message,
                meta={"route": route},
            )
        )
    budget = llm.RunBudget.from_limits(cfg.limits)
    result, r, cost = llm.call(
        agent_id=agent_id,
        purpose="chat",
        spec_tier="small",
        risk="LOW",
        attempt=1,
        system=system,
        user=user_prompt,
        schema=SCHEMA,
        mock_output=_mock(route, facts, sources),
        budget=budget,
    )
    out = result.output
    valid_ids = {s["id"] for s in sources}
    out["source_ids"] = [i for i in out["source_ids"] if i in valid_ids]  # descarta citações inventadas
    out["fact_ids"] = [i for i in out["fact_ids"] if i in facts]
    with session_scope() as db:
        db.add(
            AgentChatMessage(
                conversation_id=conv,
                agent_id=agent_id,
                user_id=user_id,
                role="assistant",
                content=out["answer"],
                meta={
                    "route": route,
                    "model": result.model,
                    "tier": r.tier,
                    "cost_micro_usd": cost,
                    "source_ids": out["source_ids"],
                },
            )
        )
    return {
        "conversation_id": str(conv),
        "route": route,
        "answer": out["answer"],
        "sources": [
            {k: s[k] for k in ("id", "title", "url", "publisher", "date", "approval_status")}
            for s in sources
            if s["id"] in out["source_ids"]
        ],
        "facts": {k: facts[k] for k in out["fact_ids"]},
        "suggested_action": out["suggested_action"],
        "model": result.model,
        "tier": r.tier,
        "cost_micro_usd": cost,
        "llm_simulated": llm.get_provider().simulated,
    }
