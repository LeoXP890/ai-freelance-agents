"""Publicação de anúncio por plataforma parceira (Dropi) na conta do proprietário — piloto de dropshipping.

O sistema não publica pelo executor: a proposta PUBLISH_VIA_PLATFORM passa pela política (aprovação humana, evidência,
homologações de go-live em LIVE, emergency stop) e, aprovada, o agente publica pela plataforma e registra o resultado
aqui. Nada é publicado sem a proposta aprovada e válida.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.governance import audit, golive
from aicommerce.models import ApprovalProposal, Opportunity, SellerProfile

PLATFORMS = {"DROPI"}
CHANNELS = {"MERCADO_LIVRE": golive.Channel.MERCADO_LIVRE, "SHOPEE": golive.Channel.SHOPEE}


class PlatformPublishError(Exception):
    code = "PLATFORM_PUBLISH_ERROR"


def _market(o: Opportunity) -> dict[str, Any]:
    return next((s.get("market") or {} for s in o.sources or [] if isinstance(s, dict) and s.get("market")), {})


def propose(
    db: Session,
    opportunity_id: uuid.UUID,
    *,
    actor: str,
    price_minor: int,
    title: str | None = None,
    platform: str = "DROPI",
    channel: str = "MERCADO_LIVRE",
    supplier_freight_minor: int | None = None,
    request_id: str | None = None,
) -> ApprovalProposal:
    """Monta a proposta com a conta por venda CONFERIDA (tarifa e frete na API do Mercado Livre, frete do fornecedor)
    e só envia para aprovação se o lucro passar do mínimo."""
    from aicommerce.commerce import unit_economics
    from aicommerce.config import get_settings
    from aicommerce.governance.service import submit_proposal

    if platform not in PLATFORMS or channel not in CHANNELS:
        raise PlatformPublishError("plataforma ou canal não suportado no piloto")
    if price_minor <= 0:
        raise PlatformPublishError("preço de venda obrigatório")
    o = db.get(Opportunity, opportunity_id, with_for_update=True)
    if o is None:
        raise PlatformPublishError("oportunidade inexistente")
    m = _market(o)
    seller = db.execute(select(SellerProfile)).scalars().first()
    uf = (seller.uf if seller else None) or ""
    if len(uf) != 2:
        raise PlatformPublishError("UF do vendedor não cadastrada no perfil (necessária para as homologações)")
    supplier = str(m.get("supplier") or "fornecedor da plataforma")
    gtin = str(m.get("gtin") or o.id)
    final_title = (title or o.title)[:60]
    freight = supplier_freight_minor / 100 if supplier_freight_minor is not None else m.get("supplier_freight")
    try:
        econ = unit_economics.for_listing(
            db,
            agent_id="luna",
            title=final_title,
            price=price_minor / 100,
            cost=float(m.get("supplier_cost") or 0),
            supplier_freight=None if freight is None else float(freight),
            category_id=m.get("category_id"),
            box=(m.get("shipping") or {}).get("box"),
            known_seller_shipping=(m.get("shipping") or {}).get("seller_cost"),
        )
    except unit_economics.EconomicsError as exc:
        raise PlatformPublishError(str(exc)) from None
    from aicommerce.ai import tools
    from aicommerce.ai.tools import market

    try:  # preço real do mercado é obrigatório: o kit a R$ 109,90 tinha concorrentes a R$ 37 com frete grátis
        check = tools.call(db, "luna", "market.price_check", {"title": final_title})
    except tools.ToolError as exc:
        raise PlatformPublishError(f"não foi possível conferir o preço de mercado: {exc}") from None
    landed = market.landed_cost(float(m.get("supplier_cost") or 0), float(freight))
    ok, market_note = market.verdict(check, landed=landed, price=price_minor / 100)
    if not ok:
        raise PlatformPublishError(f"{market_note}: não enviado para aprovação")
    minimum = get_settings().platform_min_profit_minor
    if econ["profit_minor"] < minimum:
        raise PlatformPublishError(
            f"lucro por venda de R$ {econ['profit_minor'] / 100:.2f} abaixo do mínimo de R$ {minimum / 100:.2f}: "
            "não enviado para aprovação"
        )
    previous = db.execute(
        select(func.count())
        .select_from(ApprovalProposal)
        .where(
            ApprovalProposal.subject_type == "opportunity",
            ApprovalProposal.subject_id == str(o.id),
            ApprovalProposal.action_type == "PUBLISH_VIA_PLATFORM",
        )
    ).scalar_one()
    payload = {
        "platform": platform,
        "channel": channel,
        "title": final_title,
        "price_minor": price_minor,
        "currency": "BRL",
        "category_id": econ["category_id"],
        "supplier": supplier,
        "gtin": gtin,
        "estimated_profit_minor": econ["profit_minor"],
        "economics": econ,
        "market_check": {
            **{k: check.get(k) for k in ("query", "count", "min", "median", "max")},
            "offers": check.get("offers", [])[:6],
            "note": market_note,
        },
        "pictures": (m.get("pictures") or [])[:6],
        "competitors": {"sellers": m.get("sellers"), "price_min": m.get("price_min"), "price_max": m.get("price_max")},
        "note": "Publicado pela plataforma na conta do proprietário após aprovação; pedidos seguem ao fornecedor.",
    }
    data = {
        "action_type": "PUBLISH_VIA_PLATFORM",
        "agent_id": "luna",
        "subject_type": "opportunity",
        "subject_id": str(o.id),
        "subject_version": previous + 1,
        "payload": payload,
        "risk": "MEDIUM",
        "evidence_refs": [f"opportunity:{o.id}"]
        + [f"source:{s['id']}" for s in o.sources or [] if isinstance(s, dict)],
        "monetary_scope_minor": 0,
        "currency": "BRL",
        "channel_account_id": None,
        "idempotency_key": f"opportunity:{o.id}:publish:{platform}:{channel}:{price_minor}:v{previous + 1}",
    }
    req = golive.GoLiveRequest(
        channel=CHANNELS[channel],
        supplier_origin=golive.SupplierOrigin.NATIONAL,
        fulfillment_mode=golive.FulfillmentMode.DROPSHIP_SUPPLIER,
        uf=uf.upper(),
        product_id=f"{platform.lower()}:{gtin}",
        supplier_id=f"{platform.lower()}:{supplier}",
    )
    prop, _ = submit_proposal(db, data, actor=actor, request_id=request_id, golive_req=req)
    o.status = "PROPOSED"
    return prop


def record_published(db: Session, proposal_id: uuid.UUID, *, actor: str, listing_url: str, listing_id: str) -> None:
    """Agente registra o anúncio criado pela plataforma. Só para proposta aprovada (e não expirada/revogada)."""
    prop = db.get(ApprovalProposal, proposal_id, with_for_update=True)
    if prop is None or prop.action_type != "PUBLISH_VIA_PLATFORM":
        raise PlatformPublishError("proposta inexistente")
    if prop.status not in ("AUTHORIZED", "APPROVED"):
        raise PlatformPublishError(f"proposta em {prop.status}: não pode ser publicada")
    if not listing_url.startswith("https://"):
        raise PlatformPublishError("link do anúncio inválido")
    prop.status = "SUCCEEDED"
    o = db.get(Opportunity, uuid.UUID(prop.subject_id), with_for_update=True)
    if o is not None:
        o.status = "PUBLISHED"
    audit.record(
        db,
        actor_type="agent",
        actor_id=actor,
        action="listing.published_via_platform",
        subject_type="approval_proposal",
        subject_id=str(prop.id),
        payload={"listing_id": listing_id, "listing_url": listing_url, "platform": prop.payload.get("platform")},
    )
