"""Catálogo mestre (COM-001): edição com controle de versão, histórico imutável, arquivamento e mídia.

Concorrência otimista: toda edição informa a versão que o usuário viu; se outro salvou antes, 409 e nada muda.
Cada versão grava um snapshot append-only. Anúncios já preparados guardam cópia do produto, então uma edição
os marca com PRODUCT_CHANGED (preparar de novo / propor atualização) — o canal nunca muda sem nova aprovação.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.commerce import media as media_store
from aicommerce.governance import audit
from aicommerce.models import ChannelListing, Product, ProductVersion, Variant

EDITABLE = ("title", "description", "category", "brand", "ncm", "regulated", "regulatory_docs")
MAX_MEDIA = 10
_IMG = re.compile(r"^https://[^\s/$.?#][^\s]*\.(jpe?g|png|webp)(\?[^\s]*)?$", re.IGNORECASE)


class CatalogError(Exception):
    http = 422
    code = "CATALOG_ERROR"


class NotFound(CatalogError):
    http = 404
    code = "NOT_FOUND"


class VersionConflict(CatalogError):
    http = 409
    code = "VERSION_CONFLICT"


class Blocked(CatalogError):
    http = 409
    code = "BLOCKED"


VARIANT_EDITABLE = ("attributes", "gtin", "weight_g", "length_mm", "width_mm", "height_mm", "status")


def snapshot(p: Product, db: Session | None = None) -> dict[str, Any]:
    snap = {
        "sku_base": p.sku_base,
        **{k: getattr(p, k) for k in EDITABLE},
        "media": list(p.media or []),
        "status": p.status,
    }
    if db is not None:
        snap["variants"] = [
            {"sku": v.sku, **{k: getattr(v, k) for k in VARIANT_EDITABLE}}
            for v in db.execute(select(Variant).where(Variant.product_id == p.id).order_by(Variant.sku)).scalars()
        ]
    return snap


def validate_media(media: list[dict[str, Any]]) -> list[dict[str, str]]:
    if len(media) > MAX_MEDIA:
        raise CatalogError(f"no máximo {MAX_MEDIA} imagens por produto")
    out, seen = [], set()
    for i, m in enumerate(media, 1):
        url, alt = str(m.get("url", "")).strip(), str(m.get("alt", "")).strip()
        if not (_IMG.match(url) or media_store.is_own_url(url)):  # foto enviada pelo app: URL da própria API
            raise CatalogError(f"imagem {i}: use URL https terminando em .jpg, .png ou .webp")
        if len(alt) < 3:
            raise CatalogError(f"imagem {i}: texto alternativo obrigatório (acessibilidade e canais)")
        if url in seen:
            raise CatalogError(f"imagem {i}: duplicada")
        seen.add(url)
        out.append({"url": url, "alt": alt[:200]})
    return out


def record_version(db: Session, p: Product, *, actor: str, changed: list[str]) -> None:
    db.add(
        ProductVersion(
            product_id=p.id, version=p.version, snapshot=snapshot(p, db), changed_fields=changed, changed_by=actor
        )
    )
    db.flush()


def _get(db: Session, product_id: uuid.UUID) -> Product:
    p = db.get(Product, product_id, with_for_update=True)
    if p is None:
        raise NotFound("produto inexistente")
    return p


def _mark_listings_stale(db: Session, p: Product, variant_id: uuid.UUID | None = None) -> int:
    n = 0
    variant_ids = [variant_id] if variant_id else select(Variant.id).where(Variant.product_id == p.id)
    for lst in db.execute(select(ChannelListing).where(ChannelListing.variant_id.in_(variant_ids))).scalars():
        issues = [i for i in lst.compliance if i.get("code") != "PRODUCT_CHANGED"]
        issues.append(
            {
                "severity": "WARNING",
                "code": "PRODUCT_CHANGED",
                "message": f"produto mudou (v{p.version}) depois da preparação: prepare novamente"
                + (" e proponha atualização no canal" if lst.status == "PUBLISHED" else ""),
            }
        )
        lst.compliance = issues
        n += 1
    return n


def update(
    db: Session, product_id: uuid.UUID, *, expected_version: int, changes: dict[str, Any], actor: str
) -> tuple[Product, list[str], int]:
    from aicommerce.office import events

    p = _get(db, product_id)
    if p.version != expected_version:
        raise VersionConflict(f"produto mudou para v{p.version} enquanto você editava a v{expected_version}")
    if p.status == "ARCHIVED":
        raise Blocked("produto arquivado: reative antes de editar")
    new_media = None
    if "media" in changes and changes["media"] is not None:
        new_media = validate_media(changes.pop("media"))
    changed = [k for k, v in changes.items() if k in EDITABLE and v is not None and getattr(p, k) != v]
    if new_media is not None and new_media != (p.media or []):
        changed.append("media")
    if not changed:
        return p, [], 0
    before = {k: (p.media if k == "media" else getattr(p, k)) for k in changed}
    for k in changed:
        if k == "media":
            p.media, p.images = new_media, [m["url"] for m in new_media]
        else:
            setattr(p, k, changes[k])
    p.version += 1
    record_version(db, p, actor=actor, changed=changed)
    stale = _mark_listings_stale(db, p)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="catalog.product.updated",
        subject_type="product",
        subject_id=str(p.id),
        payload={"version": p.version, "changed": changed, "before": before, "listings_marked": stale},
    )
    events.emit(
        db,
        "catalog.product.updated",
        agent_id="nova",
        payload={"product_id": str(p.id), "version": p.version, "changed": changed},
    )
    return p, changed, stale


def update_variant(
    db: Session, variant_id: uuid.UUID, *, expected_version: int, changes: dict[str, Any], actor: str
) -> tuple[Product, Variant, list[str]]:
    """Variante muda a versão do produto (mesmo controle otimista e histórico)."""
    v = db.get(Variant, variant_id, with_for_update=True)
    if v is None:
        raise NotFound("variante inexistente")
    p = _get(db, v.product_id)
    if p.version != expected_version:
        raise VersionConflict(f"produto mudou para v{p.version} enquanto você editava a v{expected_version}")
    if p.status == "ARCHIVED":
        raise Blocked("produto arquivado: reative antes de editar")
    if changes.get("status") == "INACTIVE":
        live = (
            db.execute(
                select(ChannelListing.channel).where(
                    ChannelListing.variant_id == v.id, ChannelListing.status.in_(("PUBLISHED", "PENDING_APPROVAL"))
                )
            )
            .scalars()
            .all()
        )
        if live:
            raise Blocked(f"pause os anúncios da variante antes de desativá-la: {sorted(set(live))}")
    changed = [k for k, val in changes.items() if k in VARIANT_EDITABLE and val is not None and getattr(v, k) != val]
    if not changed:
        return p, v, []
    before = {k: getattr(v, k) for k in changed}
    for k in changed:
        setattr(v, k, changes[k])
    db.flush()
    p.version += 1
    fields = [f"variant:{v.sku}:{k}" for k in changed]
    record_version(db, p, actor=actor, changed=fields)
    stale = _mark_listings_stale(db, p, v.id)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="catalog.variant.updated",
        subject_type="variant",
        subject_id=str(v.id),
        payload={"product_version": p.version, "before": before, "listings_marked": stale},
    )
    return p, v, fields


def set_status(db: Session, product_id: uuid.UUID, *, archived: bool, actor: str, reason: str) -> Product:
    p = _get(db, product_id)
    target = "ARCHIVED" if archived else "ACTIVE"
    if p.status == target:
        return p
    if archived:
        variant_ids = select(Variant.id).where(Variant.product_id == p.id)
        live = (
            db.execute(
                select(ChannelListing.channel).where(
                    ChannelListing.variant_id.in_(variant_ids),
                    ChannelListing.status.in_(("PUBLISHED", "PENDING_APPROVAL")),
                )
            )
            .scalars()
            .all()
        )
        if live:
            raise Blocked(f"pause ou retire os anúncios antes de arquivar: {sorted(set(live))}")
    p.status = target
    p.version += 1
    record_version(db, p, actor=actor, changed=["status"])
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="catalog.product.archived" if archived else "catalog.product.reactivated",
        subject_type="product",
        subject_id=str(p.id),
        payload={"reason": reason, "version": p.version},
    )
    return p


def history(db: Session, product_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ProductVersion).where(ProductVersion.product_id == product_id).order_by(ProductVersion.version.desc())
    ).scalars()
    return [
        {
            "version": v.version,
            "changed_fields": v.changed_fields,
            "changed_by": v.changed_by,
            "at": v.created_at.isoformat(),
            "snapshot": v.snapshot,
        }
        for v in rows
    ]
