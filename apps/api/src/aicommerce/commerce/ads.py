"""Campanhas de mídia (COM-008): registro após execução aprovada, métricas diárias, gasto no ledger e conciliação.

O teto da campanha é a reserva aprovada (CREATE_CAMPAIGN). Cada dia importado vira um SPEND idempotente que consome
a reserva; gasto acima do teto é registrado (já aconteceu na plataforma) e vira alerta crítico — nunca é escondido.
Pausar é sempre uma proposta PAUSE_MEDIA (delegável; sem delegação, vai para aprovação humana).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.governance import audit
from aicommerce.models import (
    ApprovalProposal,
    AuthorizationGrant,
    Campaign,
    CampaignMetric,
    LedgerEntry,
)

NEAR_CAP_BPS = 9_000  # 90% do teto


class AdsError(Exception):
    http = 422


class NotFound(AdsError):
    http = 404


def on_execution_result(db: Session, prop: ApprovalProposal, *, succeeded: bool, provider_ref: str | None) -> None:
    """Hook do executor para CREATE_CAMPAIGN e PAUSE_MEDIA."""
    from aicommerce.office import events

    if prop.action_type == "CREATE_CAMPAIGN" and succeeded:
        if db.execute(select(Campaign.id).where(Campaign.proposal_id == prop.id)).first():
            return  # replay/reconciliação: já registrada
        grant = db.execute(
            select(AuthorizationGrant).where(AuthorizationGrant.proposal_id == prop.id)
        ).scalar_one_or_none()
        p = prop.payload
        c = Campaign(
            proposal_id=prop.id,
            subject_id=prop.subject_id,
            name=str(p.get("name") or prop.subject_id)[:200],
            provider="mock-ads" if prop.mode == "DRY_RUN" else "ads",
            external_id=provider_ref,
            status="ACTIVE",
            daily_budget_minor=int(p.get("daily_budget_minor") or 0),
            cap_minor=prop.monetary_scope_minor,
            currency=prop.currency,
            budget_account_id=prop.budget_account_id,
            reservation_id=grant.reservation_id if grant else None,
            is_simulated=prop.mode == "DRY_RUN",
        )
        db.add(c)
        db.flush()
        events.emit(db, "campaign.created", agent_id="pixel", payload={"campaign_id": str(c.id), "name": c.name})
    elif prop.action_type == "PAUSE_MEDIA" and prop.subject_type == "campaign":
        c = _by_ref(db, prop.subject_id, lock=True)
        if c is None:
            return
        if succeeded:
            c.status = "PAUSED"
            events.emit(db, "campaign.paused", agent_id="pixel", payload={"campaign_id": str(c.id)})
        elif c.status == "PAUSE_REQUESTED":
            c.status = "ERROR"  # pausa falhou: segue ativa na plataforma até nova ação — vira alerta


def _by_ref(db: Session, ref: str, *, lock: bool = False) -> Campaign | None:
    try:
        cid = uuid.UUID(ref)
    except ValueError:
        return None
    return db.get(Campaign, cid, with_for_update=lock)


def get(db: Session, campaign_id: uuid.UUID, *, lock: bool = False) -> Campaign:
    c = db.get(Campaign, campaign_id, with_for_update=lock)
    if c is None:
        raise NotFound("campanha inexistente")
    return c


def propose_pause(db: Session, c: Campaign, *, actor: str, reason: str, request_id: str | None) -> ApprovalProposal:
    """Solicita a pausa pela governança (PAUSE_MEDIA). Não altera a plataforma diretamente."""
    from aicommerce.governance.service import submit_proposal

    if c.status not in ("ACTIVE", "ERROR"):
        raise AdsError(f"campanha em estado {c.status} não pode ser pausada")
    nonce = hashlib.sha256(f"{c.id}:{dt.datetime.now(dt.UTC).isoformat()}".encode()).hexdigest()[:12]
    data = {
        "action_type": "PAUSE_MEDIA",
        "agent_id": "pixel",
        "subject_type": "campaign",
        "subject_id": str(c.id),
        "subject_version": 1,
        "payload": {"campaign_external_id": c.external_id, "name": c.name, "reason": reason[:300]},
        "risk": "LOW",
        "evidence_refs": [f"campaign:{c.id}"],
        "monetary_scope_minor": 0,
        "currency": c.currency,
        "channel_account_id": None,
        "idempotency_key": f"campaign:{c.id}:pause:{nonce}",
    }
    prop, _ = submit_proposal(db, data, actor=actor, request_id=request_id, golive_req=None)
    if prop.status not in ("DENIED", "REJECTED"):
        c.status, c.pause_proposal_id = "PAUSE_REQUESTED", prop.id
    return prop


def import_metrics(
    db: Session,
    c: Campaign,
    *,
    day: dt.date,
    impressions: int,
    clicks: int,
    conversions: int,
    revenue_minor: int,
    spend_minor: int,
    source: str,
    actor: str,
) -> dict[str, Any]:
    """Importa o dia (idempotente por campanha+dia). Gasto entra no ledger mesmo com kill switch: já ocorreu."""
    from aicommerce.finance import ledger

    if min(impressions, clicks, conversions, revenue_minor, spend_minor) < 0 or clicks > impressions:
        raise AdsError("métricas inválidas (negativas ou cliques > impressões)")
    existing = db.execute(
        select(CampaignMetric).where(CampaignMetric.campaign_id == c.id, CampaignMetric.day == day)
    ).scalar_one_or_none()
    if existing:
        # Uma repetição idempotente precisa coincidir em TODOS os valores persistidos;
        # conversões e receita também alteram os indicadores de desempenho/ROAS.
        same = (
            existing.impressions,
            existing.clicks,
            existing.conversions,
            existing.revenue_minor,
            existing.spend_minor,
        ) == (impressions, clicks, conversions, revenue_minor, spend_minor)
        if not same:
            raise AdsError(f"métricas de {day} já importadas com valores diferentes (corrija na origem)")
        return {"metric_id": existing.id, "replayed": True, "overspend": False}
    entry_id, overspend = None, False
    if spend_minor > 0 and c.budget_account_id:
        resv_id = c.reservation_id
        if resv_id:
            from aicommerce.models import BudgetReservation

            resv = db.get(BudgetReservation, resv_id)
            if resv is None or resv.status != "ACTIVE":
                resv_id = None  # reserva já consumida/liberada: o gasto ainda é registrado (excedente)
        r = ledger.record_spend(
            db,
            c.budget_account_id,
            amount_minor=spend_minor,
            currency=c.currency,
            external_ref=f"campaign:{c.id}:{day.isoformat()}",
            idempotency_key=f"adspend:{c.id}:{day.isoformat()}",
            actor=actor,
            reservation_id=resv_id,
        )
        entry_id, overspend = r["entry_id"], r["overspend"]
    m = CampaignMetric(
        campaign_id=c.id,
        day=day,
        impressions=impressions,
        clicks=clicks,
        conversions=conversions,
        revenue_minor=revenue_minor,
        spend_minor=spend_minor,
        source=source,
        ledger_entry_id=entry_id,
    )
    db.add(m)
    db.flush()
    total = spent(db, c.id)
    if total >= c.cap_minor and c.status == "ACTIVE":
        c.status = "ENDED"
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="campaign.metrics.imported",
        subject_type="campaign",
        subject_id=str(c.id),
        payload={"day": day.isoformat(), "spend_minor": spend_minor, "total_spend_minor": total, "source": source},
    )
    return {"metric_id": m.id, "replayed": False, "overspend": overspend or total > c.cap_minor}


def simulate_day(c: Campaign, day: dt.date) -> dict[str, int]:
    """Métricas fictícias determinísticas (DRY_RUN): mesmo dia → mesmos números."""
    h = int(hashlib.sha256(f"{c.id}:{day.isoformat()}".encode()).hexdigest(), 16)
    spend = c.daily_budget_minor * (80 + h % 21) // 100  # 80–100% do diário
    impressions = 800 + h % 1200
    clicks = max(1, impressions * (8 + h % 17) // 1000)  # CTR 0,8–2,4%
    conversions = clicks * (h % 6) // 100
    return {
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "revenue_minor": conversions * (4_000 + h % 6_000),
        "spend_minor": spend,
    }


def spent(db: Session, campaign_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(CampaignMetric.spend_minor), 0)).where(
                CampaignMetric.campaign_id == campaign_id
            )
        ).scalar_one()
    )


def ledger_spent(db: Session, campaign_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
                LedgerEntry.entry_type == "SPEND", LedgerEntry.external_ref.like(f"campaign:{campaign_id}:%")
            )
        ).scalar_one()
    )


def summary(db: Session, c: Campaign) -> dict[str, Any]:
    rows = db.execute(
        select(CampaignMetric).where(CampaignMetric.campaign_id == c.id).order_by(CampaignMetric.day)
    ).scalars()
    days = [
        {
            "day": m.day.isoformat(),
            "impressions": m.impressions,
            "clicks": m.clicks,
            "conversions": m.conversions,
            "revenue_minor": m.revenue_minor,
            "spend_minor": m.spend_minor,
            "source": m.source,
        }
        for m in rows
    ]
    imp = sum(d["impressions"] for d in days)
    clk = sum(d["clicks"] for d in days)
    conv = sum(d["conversions"] for d in days)
    rev = sum(d["revenue_minor"] for d in days)
    spend = sum(d["spend_minor"] for d in days)
    led = ledger_spent(db, c.id)
    return {
        "id": str(c.id),
        "name": c.name,
        "status": c.status,
        "external_id": c.external_id,
        "provider": c.provider,
        "simulated": c.is_simulated,
        "daily_budget_minor": c.daily_budget_minor,
        "cap_minor": c.cap_minor,
        "spend_minor": spend,
        "cap_used_bps": spend * 10_000 // c.cap_minor if c.cap_minor else 0,
        "impressions": imp,
        "clicks": clk,
        "conversions": conv,
        "revenue_minor": rev,
        # métricas derivadas em inteiros: CTR em bps, CPC em centavos, ROAS em centésimos (x100)
        "ctr_bps": clk * 10_000 // imp if imp else None,
        "cpc_minor": spend // clk if clk else None,
        "roas_x100": rev * 100 // spend if spend else None,
        "ledger_spend_minor": led,
        "reconciled": led == spend,
        "pause_proposal_id": str(c.pause_proposal_id) if c.pause_proposal_id else None,
        "created_at": c.created_at.isoformat(),
        "days": days,
    }


def alerts(db: Session, *, emergency_stop: bool) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for c in db.execute(select(Campaign)).scalars():
        s = spent(db, c.id)
        ref = f"campaign:{c.id}"
        if c.cap_minor and s > c.cap_minor:
            out.append(("CRITICAL", "CAMPAIGN_OVERSPEND", f"campanha '{c.name}' gastou {s} de teto {c.cap_minor}", ref))
        elif c.status == "ACTIVE" and c.cap_minor and s * 10_000 >= c.cap_minor * NEAR_CAP_BPS:
            out.append(("WARNING", "CAMPAIGN_NEAR_CAP", f"campanha '{c.name}' usou ≥90% do teto", ref))
        if ledger_spent(db, c.id) != s:
            out.append(("WARNING", "CAMPAIGN_SPEND_UNRECONCILED", f"gasto da campanha '{c.name}' ≠ ledger", ref))
        if emergency_stop and c.status in ("ACTIVE", "PAUSE_REQUESTED", "ERROR"):
            out.append(
                ("CRITICAL", "CAMPAIGN_ACTIVE_DURING_STOP", f"campanha '{c.name}' ainda não pausada ({c.status})", ref)
            )
        elif c.status == "ERROR":
            out.append(("CRITICAL", "CAMPAIGN_PAUSE_FAILED", f"pausa da campanha '{c.name}' falhou", ref))
    return out
