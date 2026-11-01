"""Auditoria dos 13 agentes (finalização técnica, seção 6) — gerada a partir do código e do banco, não declarada à mão.

Para cada agente confere: papel, configuração versionada, ferramentas autorizadas, entrada/saída estruturada,
tratamento de erro (validador + red-team), persistência, custo no ledger, histórico, evidências, missões,
aprovações e representação no app. Classifica em níveis SEPARADOS (metadados de validação; não muda nenhuma
máquina de estados de negócio):

* ENGINEERING_READY  — runtime e contratos validados (todas as checagens de engenharia passam);
* MOCK_EVALUATED     — tarefas avaliadas offline com o provedor SIMULADO + red-team recusado;
* REAL_MODEL_PENDING — qualidade com modelo real NÃO avaliada (o desenvolvimento não usa IA paga);
* PRODUCTION_READY   — só com avaliação real + homologações externas aplicáveis (nunca declarado aqui).

Uso: python -m aicommerce.ai.agent_audit [--eval progress/test-reports/ai_eval.json] [--report saida.json]
O relatório oficial é gerado por tests/test_agent_audit.py, que cria os dados de que as tarefas precisam (num banco
recém-criado, sem anúncio/pedido/missão, os casos desses agentes seriam pulados e o resultado seria incompleto).
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from sqlalchemy import func, select

from aicommerce.ai.evals import RED_TEAM
from aicommerce.ai.runtime import _config
from aicommerce.ai.specs import PLANNABLE, SPECS
from aicommerce.db import session_scope
from aicommerce.models import Agent, AgentConfig, AgentTask, AIUsage

ROOT = pathlib.Path(__file__).resolve().parents[5]
ALWAYS_ALLOWED_TOOLS = ("catalog.query", "orders.read")  # leitura somente, liberada a todos (A08)
# Quem PROPÕE ações executáveis (vão para a Central de Aprovações com MFA); os demais são consultivos.
APPROVAL_PATHS = {
    "pixel": "CREATE_CAMPAIGN / PAUSE_MEDIA (commerce/ads.py, ai/specs.py)",
    "nova": "PUBLISH_LISTING / PAUSE_LISTING (commerce/listings.py)",
    "logan": "SUPPLIER_PURCHASE (commerce/orders.py)",
    "eva": "REFUND (commerce/orders.py)",
    "luna": "proposta de oportunidade (api/routes_ai.py)",
    "atlas": "orquestra; decisões chegam ao proprietário pela Sala do CEO/Aprovações",
}
# Validadores que exigem evidência (ids de fonte/fato) ou regra objetiva antes de aceitar a saída.
EVIDENCE_RULE = {
    "research.opportunities": "cita fontes existentes; evidência simulada não pode ser ALTA",
    "supplier.shortlist": "cita o dossiê; risco não-BAIXO exige riscos listados",
    "supplier.discover": "cita fonte real, jamais homologa lead como fornecedor",
    "commercial.model": "preço/margem iguais ao cálculo determinístico",
    "finance.brief": "cita fact_ids; números do texto presentes nos fatos citados",
    "analytics.report": "cita fact_ids; números do texto presentes nos fatos citados",
    "logistics.sla_check": "cita fact_ids; números do texto presentes nos fatos citados",
    "compliance.review": "tema fiscal/jurídico escala para revisão humana; cita a base",
    "listing.audit": "não omite pendência impeditiva; nota coerente",
    "creative.copy": "bloqueia promessas proibidas",
    "support.reply_draft": "não promete reembolso fora da política",
    "ads.plan": "orçamento dentro do teto aprovado",
    "mission.plan": "só tarefas do catálogo, dentro do número de passos",
    "mission.summarize": "cita fact_ids; números do texto presentes nos fatos",
    "channel.plan": "cita fatos do canal; sem número sem evidência; sem PREPARAR sem homologação em LIVE",
}


def _skill_inputs() -> dict[str, str]:
    """Tarefas acionáveis pelo painel de habilidades do workspace do agente (apps/web/lib/format.ts → SKILL_INPUT)."""
    import re

    src = (ROOT / "apps/web/lib/format.ts").read_text()
    block = src[src.index("SKILL_INPUT") : src.index("};", src.index("SKILL_INPUT"))]
    return dict(re.findall(r'"([a-z_.]+)":\s*"([a-z]+)"', block))


def _frontend_has(agent_id: str) -> dict[str, bool]:
    identity = (ROOT / "apps/web/lib/agentIdentity.ts").read_text()
    character = (ROOT / "apps/web/desktop/office/Character.tsx").read_text()
    look = (ROOT / "apps/web/lib/officeHq.ts").read_text() + character
    return {"identity": f"{agent_id}:" in identity, "scene": agent_id in look or f'"{agent_id}"' in identity}


def audit(eval_report: dict[str, Any] | None) -> dict[str, Any]:
    eval_by_kind: dict[str, list[str]] = {}
    for t in (eval_report or {}).get("tasks", []):
        eval_by_kind.setdefault(t["kind"], []).append(t["status"])
    red_by_kind: dict[str, int] = {}
    for kind, *_ in RED_TEAM:
        red_by_kind[kind] = red_by_kind.get(kind, 0) + 1
    red_ok = all(r["rejected"] for r in (eval_report or {}).get("red_team", [])) if eval_report else False
    skills = _skill_inputs()
    rows = []
    with session_scope() as db:
        for agent in db.execute(select(Agent).order_by(Agent.id)).scalars():
            kinds = [k for k, s in SPECS.items() if s.agent_id == agent.id]
            cfg = _config(db, agent.id)
            versions = db.execute(
                select(func.count()).select_from(AgentConfig).where(AgentConfig.agent_id == agent.id)
            ).scalar_one()
            tools_needed = sorted({t for k in kinds for t in SPECS[k].tools})
            tools_missing = [
                t for t in tools_needed if t not in (cfg.tools if cfg else []) and t not in ALWAYS_ALLOWED_TOOLS
            ]
            usage = db.execute(
                select(func.count()).select_from(AIUsage).where(AIUsage.agent_id == agent.id)
            ).scalar_one()
            tasks = db.execute(
                select(func.count()).select_from(AgentTask).where(AgentTask.agent_id == agent.id)
            ).scalar_one()
            fe = _frontend_has(agent.id)
            checks = {
                "role_defined": bool(agent.role.strip()),
                "config_versioned": bool(cfg and cfg.content_sha256 and cfg.version >= 1),
                "tools_authorized": not tools_missing,
                "structured_io": bool(kinds) and all(SPECS[k].schema.get("properties") for k in kinds),
                "error_handling": all(SPECS[k].validate and red_by_kind.get(k, 0) for k in kinds),
                "persistence": all(SPECS[k].persist for k in kinds),
                "cost_in_ledger": usage > 0,
                "history": tasks > 0 or usage > 0,
                "evidence_rule": all(k in EVIDENCE_RULE for k in kinds),
                # acionável: etapa de missão do Atlas OU habilidade no workspace (com o registro que exige)
                "orchestration": all(
                    agent.id == "atlas" or k in PLANNABLE or k in skills for k in kinds if k != "mission.summarize"
                ),
                "frontend": fe["identity"] and fe["scene"],
            }
            engineering = all(checks.values())
            evaluated = bool(kinds) and all(eval_by_kind.get(k) and set(eval_by_kind[k]) == {"PASS"} for k in kinds)
            rows.append(
                {
                    "agent": agent.id,
                    "name": agent.name,
                    "room": agent.room,
                    "task_kinds": kinds,
                    "config_version": cfg.version if cfg else None,
                    "config_versions": versions,
                    "tools": tools_needed,
                    "tools_missing": tools_missing,
                    "evidence_rules": {k: EVIDENCE_RULE.get(k) for k in kinds},
                    "red_team_cases": {k: red_by_kind.get(k, 0) for k in kinds},
                    "approval_path": APPROVAL_PATHS.get(agent.id, "consultivo: recomenda, não executa ações"),
                    "triggered_by": {
                        k: "missão (Atlas)"
                        if k in PLANNABLE
                        else f"habilidade no workspace (exige {skills[k]})"
                        if skills.get(k, "none") != "none"
                        else "habilidade no workspace"
                        if k in skills
                        else "orquestrador"
                        for k in kinds
                    },
                    "checks": checks,
                    "levels": {
                        "ENGINEERING_READY": engineering,
                        "MOCK_EVALUATED": evaluated and red_ok,
                        "REAL_MODEL_PENDING": True,
                        "PRODUCTION_READY": False,
                    },
                    "production_note": "exige avaliação com o modelo real (chave do proprietário) e homologações "
                    "externas aplicáveis; não avaliado no desenvolvimento por regra (sem IA paga)",
                }
            )
    return {
        "eval_provider": (eval_report or {}).get("provider"),
        "eval_simulated": (eval_report or {}).get("simulated"),
        "agents": rows,
        "summary": {
            "agents": len(rows),
            "engineering_ready": sum(r["levels"]["ENGINEERING_READY"] for r in rows),
            "mock_evaluated": sum(r["levels"]["MOCK_EVALUATED"] for r in rows),
            "real_model_pending": sum(r["levels"]["REAL_MODEL_PENDING"] for r in rows),
            "production_ready": 0,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", type=pathlib.Path, default=ROOT / "progress/test-reports/ai_eval.json")
    ap.add_argument("--report", type=pathlib.Path)
    a = ap.parse_args()
    ev = json.loads(a.eval.read_text()) if a.eval.exists() else None
    r = audit(ev)
    if a.report:
        a.report.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(r["summary"], ensure_ascii=False, indent=2))
    for x in r["agents"]:
        bad = [k for k, v in x["checks"].items() if not v]
        levels = [k for k, v in x["levels"].items() if v]
        print(f"{x['agent']:7} {'OK' if not bad else 'FALTA ' + ','.join(bad)}  níveis={levels}")
    raise SystemExit(0 if r["summary"]["engineering_ready"] == r["summary"]["agents"] else 1)


if __name__ == "__main__":
    main()
