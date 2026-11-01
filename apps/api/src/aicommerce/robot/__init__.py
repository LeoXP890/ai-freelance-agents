"""Robô de publicação (F5): executa no painel do Dropi as propostas PUBLISH_VIA_PLATFORM APROVADAS.

Só age em LIVE, sem parada de emergência, dentro da validade da proposta, exatamente com título/preço aprovados
(o payload tem hash: o que o proprietário aprovou é o que vai). Passos: importar → título → preço → categoria do
Mercado Livre → marca/modelo → salvar exportando para o Mercado Livre → conferir o anúncio na conta (API de leitura)
→ registrar o link (platform_publish.record_published). Para quando precisa do proprietário (termos do fornecedor,
atributo obrigatório, divergência) e nunca tenta contornar. Uma foto de tela por passo fica como evidência.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.models import ApprovalProposal, Opportunity, RobotRun

MAX_ATTEMPTS = 2


@dataclass
class Job:
    proposal_id: uuid.UUID
    title: str
    price_minor: int
    category_id: str
    category_path: list[str]
    brand: str
    model: str
    import_url: str | None
    dropi_title: str | None
    edit_url: str | None = None


@dataclass
class Outcome:
    state: str  # DONE | NEEDS_OWNER | FAILED | REHEARSED
    message: str
    step: str
    edit_url: str | None = None
    evidence: list[str] = field(default_factory=list)


class Driver(Protocol):
    def publish(self, job: Job, *, rehearse: bool) -> Outcome: ...


def _market(o: Opportunity | None) -> dict[str, Any]:
    if o is None:
        return {}
    return next((s.get("market") or {} for s in o.sources or [] if isinstance(s, dict) and s.get("market")), {})


def build_job(db: Session, prop: ApprovalProposal, category_path: list[str]) -> Job:
    import re

    p = prop.payload
    o = db.get(Opportunity, uuid.UUID(prop.subject_id))
    m = _market(o)
    code = re.search(r"\(([^)]+)\)", (o.title if o else "") or "")
    return Job(
        proposal_id=prop.id,
        title=str(p["title"])[:60],
        price_minor=int(p["price_minor"]),
        category_id=str(p.get("category_id") or m.get("category_id") or ""),
        category_path=category_path,
        brand=str(m.get("brand") or "Genérica")[:40],
        model=(code.group(1) if code else str(p["title"]).split(" ")[0])[:40],
        import_url=m.get("dropi_import_url"),
        dropi_title=o.title if o else None,
    )


def verified(payload: dict[str, Any]) -> bool:
    """Só publica o que tem conta por venda e pesquisa de mercado conferidas na proposta (desde 01/11/2026)."""
    econ, market = payload.get("economics") or {}, payload.get("market_check") or {}
    return bool(payload.get("category_id")) and econ.get("profit_minor", 0) > 0 and (market.get("count") or 0) >= 2


def eligible(db: Session, now: dt.datetime) -> ApprovalProposal | None:
    """Próxima proposta aprovada que o robô deve executar (nunca em DRY_RUN nem com parada de emergência)."""
    from aicommerce.config import get_settings
    from aicommerce.governance import killswitch

    if get_settings().is_dry_run or killswitch.is_active(db):
        return None
    rows = db.execute(
        select(ApprovalProposal)
        .where(
            ApprovalProposal.action_type == "PUBLISH_VIA_PLATFORM",
            ApprovalProposal.status == "AUTHORIZED",
            ApprovalProposal.expires_at > now,
        )
        .order_by(ApprovalProposal.created_at)
        .with_for_update(skip_locked=True)
    ).scalars()
    for prop in rows:
        run = db.get(RobotRun, prop.id)
        if run is None or (run.state == "FAILED" and run.attempts < MAX_ATTEMPTS):
            return prop
    return None


def _save(db: Session, prop_id: uuid.UUID, **fields: Any) -> RobotRun:
    run = db.get(RobotRun, prop_id, with_for_update=True)
    if run is None:
        run = RobotRun(proposal_id=prop_id, state="RUNNING", attempts=0, evidence=[])
        db.add(run)
    for k, v in fields.items():
        setattr(run, k, v)
    db.flush()
    return run


def category_path(db: Session, category_id: str) -> list[str]:
    from aicommerce.ai.tools import ml

    data = ml._get(f"/categories/{category_id}")
    return [str(x["name"]) for x in data.get("path_from_root") or []]


def run_once(driver: Driver, *, now: dt.datetime | None = None) -> str | None:
    """Executa no máximo uma publicação. Devolve o estado final (ou None se não havia nada a fazer)."""
    from aicommerce.ai import tools
    from aicommerce.commerce import platform_publish
    from aicommerce.db import session_scope
    from aicommerce.governance import audit

    now = now or dt.datetime.now(dt.UTC)
    with session_scope() as db:
        prop = eligible(db, now)
        if prop is None:
            return None
        run = db.get(RobotRun, prop.id)
        attempts = (run.attempts if run else 0) + 1
        if not verified(prop.payload):  # aprovada antes da conta/pesquisa de mercado obrigatórias: nunca publica
            _save(
                db,
                prop.id,
                state="NEEDS_OWNER",
                step="conferência",
                attempts=attempts,
                message="proposta aprovada sem a conta por venda e a pesquisa de mercado conferidas: o robô não "
                "publica; rejeite ou peça uma proposta nova",
            )
            return "NEEDS_OWNER"
        try:
            path = category_path(db, str(prop.payload["category_id"]))
        except Exception as exc:
            _save(
                db,
                prop.id,
                state="FAILED",
                step="categoria",
                attempts=attempts,
                message=f"não consegui ler a categoria no Mercado Livre ({type(exc).__name__})",
            )
            return "FAILED"
        job = build_job(db, prop, path)
        job.edit_url = run.dropi_edit_url if run else None
        _save(db, prop.id, state="RUNNING", step="início", attempts=attempts, message=None)
    try:
        out = driver.publish(job, rehearse=False)
    except Exception as exc:  # o robô nunca derruba o serviço: registra e para
        out = Outcome("FAILED", f"{type(exc).__name__}: {str(exc)[:300]}", "erro inesperado")
    with session_scope() as db:
        listing: dict[str, Any] = {}
        if out.state == "DONE":
            try:
                listing = tools.call(db, "nova", "ml.my_listing_find", {"title": job.title})
            except tools.ToolError as exc:
                listing = {"found": False, "error": str(exc)}
            if not listing.get("found"):
                out = Outcome(
                    "NEEDS_OWNER",
                    "Dropi aceitou a exportação, mas o anúncio ainda não apareceu na sua "
                    "conta do Mercado Livre; confira no Dropi (Produtos → Marketplaces)",
                    "conferir anúncio",
                    out.edit_url,
                    out.evidence,
                )
            elif abs(float(listing.get("price") or 0) * 100 - job.price_minor) > 1:
                out = Outcome(
                    "NEEDS_OWNER",
                    f"anúncio publicado com preço R$ {listing.get('price')} diferente do "
                    f"aprovado (R$ {job.price_minor / 100:.2f}): corrigir antes de vender",
                    "conferir preço",
                    out.edit_url,
                    out.evidence,
                )
        run = _save(
            db,
            job.proposal_id,
            state=out.state,
            step=out.step,
            message=out.message,
            dropi_edit_url=out.edit_url,
            evidence=out.evidence,
            listing_id=listing.get("listing_id"),
            listing_url=listing.get("permalink"),
        )
        if out.state == "DONE":
            platform_publish.record_published(
                db,
                job.proposal_id,
                actor="robot:dropi",
                listing_url=str(listing["permalink"]),
                listing_id=str(listing["listing_id"]),
            )
        audit.record(
            db,
            actor_type="agent",
            actor_id="robot:dropi",
            action=f"robot.publish.{out.state.lower()}",
            subject_type="approval_proposal",
            subject_id=str(job.proposal_id),
            payload={
                "step": out.step,
                "message": out.message,
                "attempt": run.attempts,
                "listing_id": listing.get("listing_id"),
            },
        )
    return out.state


def main() -> None:
    import argparse
    import logging
    import time

    from aicommerce.observability import configure_json_logging
    from aicommerce.robot.dropi_driver import PlaywrightDropi

    parser = argparse.ArgumentParser()
    parser.add_argument("--rehearse", help="ensaio sem publicar: título do produto já importado no Dropi")
    parser.add_argument("--price", type=float, default=0)
    parser.add_argument("--category", default="")
    parser.add_argument("--edit-url", default=None, help="página de edição do produto já importado no Dropi")
    args = parser.parse_args()
    configure_json_logging()
    log = logging.getLogger("aicommerce.robot")
    driver = PlaywrightDropi()
    if args.rehearse:  # ensaio: faz tudo menos o clique final de exportar
        from aicommerce.db import session_scope

        with session_scope() as db:
            path = category_path(db, args.category)
        job = Job(
            uuid.uuid4(),
            args.rehearse[:60],
            round(args.price * 100),
            args.category,
            path,
            "Genérica",
            args.rehearse.split(" ")[0],
            None,
            args.rehearse,
            args.edit_url,
        )
        out = driver.publish(job, rehearse=True)
        print(out.state, "|", out.step, "|", out.message, "|", out.evidence)
        return
    log.info("robô de publicação iniciado")
    while True:
        try:
            state = run_once(driver)
        except Exception as exc:  # banco fora etc.: espera e tenta de novo
            log.error("ciclo do robô falhou: %s", type(exc).__name__)
            state = None
        time.sleep(5 if state else 30)
