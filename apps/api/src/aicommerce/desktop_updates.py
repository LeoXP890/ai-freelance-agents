"""Atualização automática do app desktop servida pela própria API (UXD-021).

O app pergunta `GET /v1/desktop/update/{target}/{arch}/{versão atual}`; havendo versão mais nova (não revogada) com
pacote para a plataforma, a resposta segue o formato do updater do Tauri (versão, notas, data, URL, assinatura).
O app só instala se a assinatura minisign bater com a chave pública embutida nele — o servidor não consegue
empurrar um pacote não assinado pela chave do proprietário. Publicação: `python -m aicommerce.manage
publish-desktop-release` (pacote + .sig gerados por scripts/build_desktop_release.sh).
"""

from __future__ import annotations

import hashlib
import pathlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import audit
from aicommerce.models import DesktopArtifact, DesktopRelease

PLATFORMS = ("darwin-aarch64", "darwin-x86_64", "windows-x86_64", "windows-aarch64", "linux-x86_64")
SEMVER = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})$")
MAX_BYTES = 150 * 1024 * 1024
FILENAME = re.compile(r"^[A-Za-z0-9._ -]{1,200}$")


class ReleaseError(ValueError):
    pass


def parse(version: str) -> tuple[int, int, int]:
    m = SEMVER.match(version.strip())
    if not m:
        raise ReleaseError(f"versão inválida: {version!r} (use MAIOR.MENOR.CORREÇÃO, ex.: 0.2.0)")
    return int(m[1]), int(m[2]), int(m[3])


def artifact_url(a: DesktopArtifact) -> str:
    from urllib.parse import quote

    return f"{get_settings().public_base_url.rstrip('/')}/v1/desktop/artifacts/{a.sha256}/{quote(a.filename)}"


def latest_for(db: Session, platform: str, current: str) -> dict | None:
    """Manifesto do Tauri para a versão mais nova que a atual, ou None (sem atualização)."""
    cur = parse(current)
    rows = db.execute(
        select(DesktopRelease, DesktopArtifact)
        .join(DesktopArtifact, DesktopArtifact.version == DesktopRelease.version)
        .where(DesktopArtifact.platform == platform, DesktopRelease.revoked_at.is_(None))
    ).all()
    best = max(((parse(r.version), r, a) for r, a in rows if parse(r.version) > cur), default=None, key=lambda x: x[0])
    if best is None:
        return None
    _, rel, art = best
    return {
        "version": rel.version,
        "notes": rel.notes,
        "pub_date": rel.published_at.isoformat().replace("+00:00", "Z"),
        "url": artifact_url(art),
        "signature": art.signature,
    }


def publish(
    db: Session, *, version: str, notes: str, artifacts: dict[str, pathlib.Path], actor: str
) -> list[DesktopArtifact]:
    """Registra a versão com um pacote por plataforma. O .sig fica ao lado do pacote (`<pacote>.sig`)."""
    parse(version)
    if not artifacts:
        raise ReleaseError("informe ao menos um pacote (plataforma=arquivo)")
    if db.get(DesktopRelease, version) is not None:
        raise ReleaseError(f"a versão {version} já foi publicada (versões são imutáveis; publique uma nova)")
    out = []
    for platform, path in artifacts.items():  # valida TUDO antes de gravar qualquer coisa
        if platform not in PLATFORMS:
            raise ReleaseError(f"plataforma desconhecida: {platform} (use {', '.join(PLATFORMS)})")
        sig_path = path.with_name(path.name + ".sig")
        if not path.is_file() or not sig_path.is_file():
            raise ReleaseError(f"faltam {path} e/ou {sig_path} (gere com scripts/build_desktop_release.sh)")
        data = path.read_bytes()
        if not 0 < len(data) <= MAX_BYTES:
            raise ReleaseError(f"pacote {path.name} vazio ou maior que 150 MB")
        if not FILENAME.match(path.name):
            raise ReleaseError(f"nome de arquivo inválido: {path.name!r}")
        signature = sig_path.read_text().strip()
        if not signature or len(signature) > 2000:
            raise ReleaseError(f"assinatura inválida em {sig_path.name}")
        out.append(
            DesktopArtifact(
                sha256=hashlib.sha256(data).hexdigest(),
                version=version,
                platform=platform,
                filename=path.name,
                signature=signature,
                data=data,
                size_bytes=len(data),
            )
        )
    db.add(DesktopRelease(version=version, notes=notes.strip()[:5000], published_by=actor))
    db.flush()
    db.add_all(out)
    db.flush()
    audit.record(
        db,
        actor_type="system",
        actor_id=actor,
        action="desktop.release.published",
        subject_type="desktop_release",
        subject_id=version,
        payload={"platforms": sorted(artifacts), "sha256": {a.platform: a.sha256 for a in out}},
    )
    return out


def revoke(db: Session, version: str, *, actor: str) -> None:
    import datetime as dt

    rel = db.get(DesktopRelease, version)
    if rel is None:
        raise ReleaseError(f"versão {version} não encontrada")
    rel.revoked_at = dt.datetime.now(dt.UTC)
    audit.record(
        db,
        actor_type="system",
        actor_id=actor,
        action="desktop.release.revoked",
        subject_type="desktop_release",
        subject_id=version,
    )
