"""Campanhas de mídia (COM-008): listagem com métricas, importação de gasto, simulação DRY_RUN e pausa."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.api.deps import request_id, require
from aicommerce.api.routes_governance import proposal_out
from aicommerce.commerce import ads
from aicommerce.config import get_settings
from aicommerce.db import get_db
from aicommerce.identity.security import Principal
from aicommerce.models import Campaign

router = APIRouter(prefix="/v1", tags=["ads"])


def _err(e: ads.AdsError) -> HTTPException:
    return HTTPException(e.http, str(e))


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db), _: Principal = Depends(require("finance:read"))):
    rows = db.execute(select(Campaign).order_by(Campaign.created_at.desc()).limit(100)).scalars()
    return {"items": [ads.summary(db, c) for c in rows]}


@router.get("/campaigns/{campaign_id}")
def get_campaign(
    campaign_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require("finance:read"))
):
    try:
        return ads.summary(db, ads.get(db, campaign_id))
    except ads.AdsError as e:
        raise _err(e) from e


class MetricsIn(BaseModel):
    day: dt.date
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: int = Field(default=0, ge=0)
    revenue_minor: int = Field(default=0, ge=0)
    spend_minor: int = Field(ge=0)


@router.post("/campaigns/{campaign_id}/metrics", status_code=status.HTTP_201_CREATED)
def import_metrics(
    campaign_id: uuid.UUID,
    body: MetricsIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("finance:reconcile")),
):
    """Importa o extrato diário da plataforma (idempotente por dia)."""
    try:
        c = ads.get(db, campaign_id, lock=True)
        r = ads.import_metrics(db, c, **body.model_dump(), source="IMPORT", actor=str(p.user_id))
    except ads.AdsError as e:
        raise _err(e) from e
    return {**r, "campaign": ads.summary(db, c)}


class SimDaysIn(BaseModel):
    days: int = Field(default=1, ge=1, le=14)


@router.post("/sim/campaigns/{campaign_id}/metrics", status_code=status.HTTP_201_CREATED)
def simulate_metrics(
    campaign_id: uuid.UUID,
    body: SimDaysIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("sim:control")),
):
    """DRY_RUN: gera N dias de métricas fictícias determinísticas a partir do dia seguinte ao último importado."""
    if not get_settings().is_dry_run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "simulação indisponível fora de DRY_RUN")
    try:
        c = ads.get(db, campaign_id, lock=True)
        last = ads.summary(db, c)["days"]
        start = dt.date.fromisoformat(last[-1]["day"]) + dt.timedelta(days=1) if last else c.created_at.date()
        out = []
        for i in range(body.days):
            if c.status not in ("ACTIVE", "PAUSE_REQUESTED", "ERROR"):
                break  # pausada/encerrada não gera mais gasto
            day = start + dt.timedelta(days=i)
            out.append(
                ads.import_metrics(db, c, day=day, **ads.simulate_day(c, day), source="SIM", actor=str(p.user_id))
            )
    except ads.AdsError as e:
        raise _err(e) from e
    return {"imported": len(out), "campaign": ads.summary(db, c)}


class PauseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/campaigns/{campaign_id}/pause", status_code=status.HTTP_201_CREATED)
def pause(
    campaign_id: uuid.UUID,
    body: PauseIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("proposals:create")),
    rid: str = Depends(request_id),
):
    try:
        prop = ads.propose_pause(
            db, ads.get(db, campaign_id, lock=True), actor=str(p.user_id), reason=body.reason, request_id=rid
        )
    except ads.AdsError as e:
        raise _err(e) from e
    return proposal_out(prop)
