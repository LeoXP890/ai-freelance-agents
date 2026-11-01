"""Atualização automática do app desktop (UXD-021). Público: o app consulta antes do login; a integridade vem da
assinatura minisign verificada pelo próprio app com a chave pública embutida nele."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from aicommerce import desktop_updates as du
from aicommerce.db import get_db
from aicommerce.models import DesktopArtifact

router = APIRouter(prefix="/v1/desktop", tags=["desktop"])


@router.get("/update/{target}/{arch}/{current_version}")
def check_update(target: str, arch: str, current_version: str, db: Session = Depends(get_db)):
    platform = f"{target}-{arch}"
    if platform not in du.PLATFORMS:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        manifest = du.latest_for(db, platform, current_version)
    except du.ReleaseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if manifest is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
    return manifest


@router.get("/artifacts/{sha256}/{filename}", include_in_schema=False)
def get_artifact(sha256: str, filename: str, db: Session = Depends(get_db)):
    art = db.get(DesktopArtifact, sha256) if len(sha256) == 64 else None
    if art is None or art.filename != filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pacote não encontrado")
    return Response(
        art.data,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'attachment; filename="{art.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
