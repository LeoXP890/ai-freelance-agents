"""Guarda das pesquisas externas dos agentes (issue #3): teto, intervalo mínimo, trilha e inventário honesto.

* Toda chamada a provedor externo de pesquisa passa por `guarded()`: antes, confere o teto (mensal para o Brave, que
  é pago — teto 0 = desligado; diário para os demais) e o intervalo mínimo entre chamadas ao mesmo provedor; depois,
  registra OK/ERROR (com o código HTTP, ex.: 401/403/429/5xx) ou BLOCKED em `research_calls`, em transação própria
  (a trilha sobrevive mesmo se a tarefa falhar). Nunca guarda a chave nem a consulta crua (só o hash).
* `inventory()` diz o estado REAL de cada provedor: `mock` (fonte simulada em uso), `unavailable` (sem credencial ou
  sem teto), `configured` (credencial e teto presentes, sem chamada real bem-sucedida registrada) e `verified` (houve
  chamada real bem-sucedida nos últimos 30 dias). "verified" é só "a API respondeu ao contrato" — não homologa
  fornecedor, canal, preço nem frete.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select, text

from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.models import ResearchCall

VERIFIED_WINDOW = dt.timedelta(days=30)

# Fatos por provedor. Só o que foi conferido (data/fonte) ou o que o próprio código comprova; o resto fica explícito
# como não verificado — nunca uma cota ou preço inventado.
PROVIDERS: dict[str, dict[str, Any]] = {
    "brave_search": {
        "endpoint": "https://api.search.brave.com/res/v1/web/search",
        "auth": "cabeçalho X-Subscription-Token (chave da API)",
        "cost": "pago: US$ 5 por 1.000 consultas no plano Search, com crédito mensal gratuito de US$ 5; exige cartão",
        "quota": "sem teto mensal publicado; limite de 50 consultas/s no plano Search",
        "checked": "2026-10-26 em https://brave.com/search/api/ (a página também cita até US$ 20 de chamadas grátis — "
        "inconsistente; tratar como pago)",
        "cap_setting": "research_brave_monthly_cap",
        "cap_period": "month",
        "paid": True,
    },
    "gemini_search": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent (google_search)",
        "auth": "cabeçalho x-goog-api-key (mesma AIC_GOOGLE_API_KEY da IA)",
        "cost": "Gemini 3: 5.000 buscas/mês grátis (compartilhadas), depois US$ 14 por 1.000 buscas (por busca)",
        "quota": "teto diário próprio (AIC_RESEARCH_GEMINI_DAILY_CAP, padrão 30 perguntas/dia) abaixo da franquia",
        "checked": "2026-10-31 em https://ai.google.dev/gemini-api/docs/pricing",
        "cap_setting": "research_gemini_daily_cap",
        "cap_period": "day",
        "paid": False,
    },
    "mercadolivre_trends": {
        "endpoint": "https://api.mercadolibre.com/trends/MLB",
        "auth": "Authorization: Bearer <access token da aplicação ML> (exigido pelo conector)",
        "cost": "sem cobrança conhecida; não verificado",
        "quota": "não verificada (documentação para desenvolvedores devolveu HTTP 403 a este agente em 2026-10-26)",
        "checked": "2026-10-26: https://developers.mercadolivre.com.br/pt_br/tendencias inacessível ao agente (403)",
        "cap_setting": "research_ml_trends_daily_cap",
        "cap_period": "day",
        "paid": False,
    },
    "melhor_envio_sandbox": {
        "endpoint": "https://sandbox.melhorenvio.com.br/api/v2/me/shipment/calculate",
        "auth": "Bearer token de aplicativo do sandbox + User-Agent com contato técnico (exigido pelo conector)",
        "cost": "sandbox sem cobrança; valores sem validade comercial",
        "quota": "não publicada na página de cotação consultada",
        "checked": "2026-10-26 em https://docs.melhorenvio.com.br/docs/cotacao-de-fretes (pede token válido do app)",
        "cap_setting": "research_me_sandbox_daily_cap",
        "cap_period": "day",
        "paid": False,
    },
}


class ResearchBlocked(RuntimeError):
    """Chamada recusada ANTES de sair: sem teto, teto atingido ou intervalo mínimo não respeitado."""


def _since(period: str, now: dt.datetime) -> dt.datetime:
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _http_status(exc: BaseException) -> int | None:
    m = re.search(r"HTTP (\d{3})", str(exc))
    return int(m.group(1)) if m else None


def guarded[T](provider: str, purpose: str, query: str, call: Callable[[], T]) -> T:
    """Reserve quota durably before HTTP; concurrent workers cannot spend the same final slot.

    IN_FLIGHT counts toward the cap, including interrupted calls whose outcome/cost is unknown.
    Do not automatically refund such reservations after a crash.
    """
    meta = PROVIDERS[provider]
    s = get_settings()
    qhash = hashlib.sha256(query.strip().lower().encode()).hexdigest()
    cap = int(getattr(s, meta["cap_setting"]))
    key = int.from_bytes(hashlib.sha256(("research:" + provider).encode()).digest()[:8], "big", signed=True)
    reason = None
    with session_scope() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        now = dt.datetime.now(dt.UTC)
        counted = ("OK", "ERROR", "IN_FLIGHT")
        used = db.scalar(
            select(func.count())
            .select_from(ResearchCall)
            .where(
                ResearchCall.provider == provider,
                ResearchCall.status.in_(counted),
                ResearchCall.created_at >= _since(meta["cap_period"], now),
            )
        )
        last = db.scalar(
            select(func.max(ResearchCall.created_at)).where(
                ResearchCall.provider == provider, ResearchCall.status.in_(counted)
            )
        )
        if cap <= 0:
            reason = f"{provider}: sem teto configurado ({meta['cap_setting']}=0) — pesquisa desligada"
        elif used >= cap:
            reason = f"{provider}: teto {'mensal' if meta['cap_period'] == 'month' else 'diário'} de {cap} atingido"
        elif last is not None and (now - last).total_seconds() < s.research_min_interval_s:
            reason = f"{provider}: intervalo mínimo de {s.research_min_interval_s}s entre chamadas"
        row = ResearchCall(
            provider=provider,
            purpose=purpose[:64],
            query_sha256=qhash,
            status="BLOCKED" if reason else "IN_FLIGHT",
            detail=reason or "",
            created_at=now,
        )
        db.add(row)
        db.flush()
        reservation = row.id
    if reason:
        raise ResearchBlocked(reason)
    try:
        result = call()
    except Exception as exc:
        with session_scope() as db:
            row = db.get(ResearchCall, reservation, with_for_update=True)
            row.status, row.http_status = "ERROR", _http_status(exc)
            row.detail = type(exc).__name__  # upstream errors may contain URLs, credentials or private queries
        raise
    with session_scope() as db:
        row = db.get(ResearchCall, reservation, with_for_update=True)
        row.status, row.http_status = "OK", 200
        row.results = len(result) if isinstance(result, list | dict) else 1
    return result


def _active(provider: str) -> tuple[bool, str]:
    """(provedor ligado na configuração, motivo quando não)."""
    s = get_settings()
    if provider == "brave_search":
        if s.research_web_source != "brave":
            return False, "AIC_RESEARCH_WEB_SOURCE não é 'brave'"
        if not s.research_brave_api_key:
            return False, "falta AIC_RESEARCH_BRAVE_API_KEY"
        if s.research_brave_monthly_cap <= 0:
            return False, "falta teto mensal (AIC_RESEARCH_BRAVE_MONTHLY_CAP) — provedor pago"
        return True, ""
    if provider == "gemini_search":
        if s.research_web_source != "gemini":
            return False, "AIC_RESEARCH_WEB_SOURCE não é 'gemini'"
        if not s.google_api_key:
            return False, "falta AIC_GOOGLE_API_KEY"
        if s.research_gemini_daily_cap <= 0:
            return False, "teto diário zerado (AIC_RESEARCH_GEMINI_DAILY_CAP)"
        return True, ""
    if provider == "mercadolivre_trends":
        if s.research_trend_source != "mercadolivre":
            return False, "tendências em modo simulado (AIC_RESEARCH_TREND_SOURCE=mock)"
        if not s.research_ml_access_token:
            return False, "falta AIC_RESEARCH_ML_ACCESS_TOKEN"
        return True, ""
    if provider == "melhor_envio_sandbox":
        if not s.research_me_sandbox_token:
            return False, "falta AIC_RESEARCH_ME_SANDBOX_TOKEN"
        if not s.research_me_user_agent:
            return False, "falta AIC_RESEARCH_ME_USER_AGENT (contato técnico)"
        return True, ""
    return False, "provedor desconhecido"


def inventory() -> list[dict[str, Any]]:
    now = dt.datetime.now(dt.UTC)
    out = []
    with session_scope() as db:
        for provider, meta in PROVIDERS.items():
            on, why = _active(provider)
            last_ok = db.execute(
                select(func.max(ResearchCall.created_at)).where(
                    ResearchCall.provider == provider, ResearchCall.status == "OK"
                )
            ).scalar_one()
            counts = dict(
                db.execute(
                    select(ResearchCall.status, func.count())
                    .where(
                        ResearchCall.provider == provider, ResearchCall.created_at >= _since(meta["cap_period"], now)
                    )
                    .group_by(ResearchCall.status)
                ).all()
            )
            if provider == "mercadolivre_trends" and get_settings().research_trend_source == "mock":
                status = "mock"
            elif not on:
                status = "unavailable"
            elif last_ok is not None and now - last_ok <= VERIFIED_WINDOW:
                status = "verified"
            else:
                status = "configured"
            cap = int(getattr(get_settings(), meta["cap_setting"]))
            out.append(
                {
                    "provider": provider,
                    "status": status,
                    "reason": why or None,
                    **{k: meta[k] for k in ("endpoint", "auth", "cost", "quota", "checked", "paid")},
                    "cap": cap,
                    "cap_period": meta["cap_period"],
                    "used_in_period": counts.get("OK", 0) + counts.get("ERROR", 0) + counts.get("IN_FLIGHT", 0),
                    "blocked_in_period": counts.get("BLOCKED", 0),
                    "last_success_at": last_ok.isoformat() if last_ok else None,
                    "note": "verified = a API respondeu ao contrato numa chamada real; não homologa fornecedor, canal, "
                    "preço nem frete",
                }
            )
    return out
