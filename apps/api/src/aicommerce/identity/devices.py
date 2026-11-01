"""Computadores lembrados (app desktop sem tela de login após a primeira vez).

O segredo do dispositivo é gerado aqui, entregue UMA vez ao app (que o guarda no cofre do sistema operacional) e
armazenado só como SHA-256. Ele vale como fator de posse: herda o nível de MFA da sessão que o criou, deixa de valer
se a senha/MFA mudarem (session_version) e pode ser revogado a qualquer momento.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.governance import audit
from aicommerce.identity.service import roles_and_permissions
from aicommerce.models import DeviceSession, User

MAX_DEVICES = 10
PREFIX = "aicd_"


class DeviceError(Exception):
    http = 400


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(db: Session, user: User, *, mfa: bool, name: str) -> tuple[DeviceSession, str]:
    _, _, requires_mfa = roles_and_permissions(db, user.id)
    if requires_mfa and not mfa:
        err = DeviceError("lembrar o computador exige uma sessão com verificação em duas etapas")
        err.http = 403
        raise err
    active = db.execute(
        select(func.count()).where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
    ).scalar_one()
    if active >= MAX_DEVICES:
        raise DeviceError(f"limite de {MAX_DEVICES} computadores lembrados; revogue algum nas Configurações")
    token = PREFIX + secrets.token_urlsafe(32)
    dev = DeviceSession(
        user_id=user.id,
        name=name.strip()[:120] or "Computador",
        token_sha256=_hash(token),
        mfa=mfa,
        session_version=user.session_version,
    )
    db.add(dev)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.device.created",
        subject_type="device",
        subject_id=str(dev.id),
        payload={"name": dev.name, "mfa": mfa},
    )
    return dev, token


def login(db: Session, token: str) -> tuple[User, DeviceSession]:
    dev = None
    if token.startswith(PREFIX):
        dev = db.execute(
            select(DeviceSession).where(DeviceSession.token_sha256 == _hash(token)).with_for_update()
        ).scalar_one_or_none()
    user = db.get(User, dev.user_id) if dev else None
    if (
        dev is None
        or dev.revoked_at is not None
        or user is None
        or not user.is_active
        or dev.session_version != user.session_version
    ):
        raise DeviceError("computador não reconhecido ou credencial revogada — entre novamente")
    dev.last_used_at = dt.datetime.now(dt.UTC)
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.device.login",
        subject_type="device",
        subject_id=str(dev.id),
        payload={"name": dev.name},
    )
    return user, dev


def list_for(db: Session, user_id: uuid.UUID) -> list[DeviceSession]:
    return list(
        db.execute(
            select(DeviceSession).where(DeviceSession.user_id == user_id).order_by(DeviceSession.created_at.desc())
        ).scalars()
    )


def revoke(db: Session, user_id: uuid.UUID, device_id: uuid.UUID, *, actor: str) -> DeviceSession:
    dev = db.get(DeviceSession, device_id, with_for_update=True)
    if dev is None or dev.user_id != user_id:
        err = DeviceError("computador inexistente")
        err.http = 404
        raise err
    if dev.revoked_at is None:
        dev.revoked_at = dt.datetime.now(dt.UTC)
        audit.record(
            db,
            actor_type="user",
            actor_id=actor,
            action="auth.device.revoked",
            subject_type="device",
            subject_id=str(dev.id),
            payload={"name": dev.name},
        )
    return dev
