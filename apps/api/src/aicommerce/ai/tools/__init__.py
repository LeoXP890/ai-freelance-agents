"""Pool de ferramentas dos agentes (docs/plans/PLANO_POOL_FERRAMENTAS_2026-10-31.md).

Código faz o trabalho pesado (buscar, filtrar, calcular); a IA recebe só o resultado compacto. Toda chamada passa
pelo executor: permissão do agente (config versionada), limite diário, cache com validade e registro em tool_calls.
Ferramentas daqui só LEEM; o que muda o mundo externo continua sendo proposta com aprovação humana.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.models import AgentConfig, ToolCache, ToolCall

ALWAYS_ALLOWED_PREFIXES = ("calc.",)  # cálculo puro: grátis, sem dado externo — liberado a todos os agentes


class ToolError(Exception):
    """Falha de ferramenta com mensagem curta e segura (sem token, URL com credencial ou corpo bruto)."""

    status = "ERROR"


class ToolDenied(ToolError):
    status = "DENIED"


class ToolLimit(ToolError):
    status = "LIMIT"


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    params: dict[str, str]
    fn: Callable[[dict[str, Any]], Any]
    effect: str = "READ"
    cost_micro_usd: int = 0
    cache_s: int = 0
    daily_limit: int = 500
    source: str = ""
    required: tuple[str, ...] = field(default_factory=tuple)
    version: int = 1  # mudou a regra da ferramenta? aumente: o cache antigo deixa de valer


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    TOOLS[tool.name] = tool
    return tool


def _key(name: str, args: dict[str, Any], version: int = 1) -> str:
    raw = json.dumps({"t": name, "a": args, "v": version}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def allowed(db: Session, agent_id: str, name: str) -> bool:
    if name.startswith(ALWAYS_ALLOWED_PREFIXES):
        return True
    cfg = (
        db.execute(select(AgentConfig).where(AgentConfig.agent_id == agent_id).order_by(AgentConfig.version.desc()))
        .scalars()
        .first()
    )
    return bool(cfg and name in (cfg.tools or []))


def call(
    db: Session,
    agent_id: str,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    task_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> Any:
    """Executa a ferramenta para o agente (permissão → limite → cache → execução) e registra a chamada."""
    _load()
    args = {k: v for k, v in (args or {}).items() if v is not None}
    now = now or dt.datetime.now(dt.UTC)
    tool = TOOLS.get(name)
    started = time.monotonic()

    def log(status: str, *, hit: bool = False, error: str | None = None, cost: int = 0) -> None:
        db.add(
            ToolCall(
                agent_id=agent_id,
                tool=name,
                args=args,
                status=status,
                cache_hit=hit,
                error=(error or None) and error[:200],
                duration_ms=int((time.monotonic() - started) * 1000),
                cost_micro_usd=cost,
                task_id=task_id,
            )
        )
        db.flush()

    if tool is None:
        log("DENIED", error="ferramenta desconhecida")
        raise ToolDenied(f"ferramenta desconhecida: {name}")
    if not allowed(db, agent_id, name):
        log("DENIED", error="não permitida ao agente")
        raise ToolDenied(f"{name} não está entre as ferramentas de {agent_id}")
    missing = [p for p in tool.required if p not in args]
    if missing:
        log("ERROR", error=f"faltam: {', '.join(missing)}")
        raise ToolError(f"{name}: faltam os parâmetros {', '.join(missing)}")
    used = db.execute(
        select(func.count())
        .select_from(ToolCall)
        .where(
            ToolCall.agent_id == agent_id,
            ToolCall.tool == name,
            ToolCall.status == "OK",
            ToolCall.cache_hit.is_(False),
            ToolCall.created_at >= now - dt.timedelta(days=1),
        )
    ).scalar_one()
    key = _key(name, args, tool.version)
    if tool.cache_s:
        hit = db.get(ToolCache, key)
        if hit is not None and hit.expires_at > now:
            log("OK", hit=True)
            return hit.result
    if used >= tool.daily_limit:
        log("LIMIT", error=f"limite diário de {tool.daily_limit}")
        raise ToolLimit(f"{name}: limite diário de {tool.daily_limit} chamadas atingido")
    try:
        result = tool.fn(args)
    except ToolError as exc:
        log(exc.status, error=str(exc))
        raise
    except (ValueError, KeyError, TypeError) as exc:
        log("ERROR", error=f"{type(exc).__name__}: {exc}")
        raise ToolError(f"{name}: entrada inválida ({exc})") from None
    if tool.cache_s:
        db.merge(ToolCache(key=key, tool=name, result=result, expires_at=now + dt.timedelta(seconds=tool.cache_s)))
    log("OK", cost=tool.cost_micro_usd)
    return result


def catalog() -> list[dict[str, Any]]:
    _load()
    return [
        {
            "name": t.name,
            "title": t.title,
            "description": t.description,
            "params": t.params,
            "effect": t.effect,
            "cost_micro_usd": t.cost_micro_usd,
            "cache_s": t.cache_s,
            "daily_limit": t.daily_limit,
            "source": t.source,
            "always_allowed": t.name.startswith(ALWAYS_ALLOWED_PREFIXES),
        }
        for t in sorted(TOOLS.values(), key=lambda t: t.name)
    ]


def _load() -> None:
    """Importa os módulos que registram ferramentas (uma vez)."""
    from aicommerce.ai.tools import calc, cj, dropi, market, ml, trends  # noqa: F401
