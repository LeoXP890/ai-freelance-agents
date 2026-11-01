"""Gestão de usuários e papéis (INF-003). Exige users:manage e impede escalada de privilégio.

Regras: só concede papel cujas permissões o próprio ator já tem; só altera quem não tem permissões além das
suas; ninguém altera os próprios papéis nem se desativa; o último proprietário ativo não pode ser removido.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.api.deps import require
from aicommerce.db import get_db
from aicommerce.governance import audit
from aicommerce.identity import mfa
from aicommerce.identity.rbac import ROLES
from aicommerce.identity.security import Principal
from aicommerce.identity.service import create_user, roles_and_permissions
from aicommerce.models import User, UserRole

router = APIRouter(prefix="/v1/users", tags=["users"])


def _perms(roles: set[str]) -> set[str]:
    return set().union(*(ROLES[r][2] for r in roles if r in ROLES)) if roles else set()


def _roles(db: Session, user_id: uuid.UUID) -> set[str]:
    return set(db.execute(select(UserRole.role_name).where(UserRole.user_id == user_id)).scalars())


def _out(db: Session, u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "display_name": u.display_name,
        "roles": sorted(_roles(db, u.id)),
        "active": u.is_active,
        "mfa_enabled": u.mfa_enabled,
        "mfa_required": roles_and_permissions(db, u.id)[2],
        "locked": u.locked_until is not None,
        "simulated": u.is_simulated,
        "created_at": u.created_at.isoformat(),
    }


def _target(db: Session, user_id: uuid.UUID, p: Principal, *, allow_self: bool = False) -> User:
    u = db.get(User, user_id, with_for_update=True)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário inexistente")
    if u.id == p.user_id and not allow_self:
        raise HTTPException(status.HTTP_409_CONFLICT, "não é permitido alterar a própria conta por aqui")
    extra = _perms(_roles(db, u.id)) - set(p.permissions)
    if extra:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "o usuário tem permissões que você não possui")
    return u


def _check_grant(roles: list[str], p: Principal) -> set[str]:
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        raise HTTPException(422, f"papéis desconhecidos: {unknown}")
    extra = _perms(set(roles)) - set(p.permissions)
    if extra:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"não é possível conceder permissões que você não tem: {sorted(extra)[:5]}"
        )
    return set(roles)


def _guard_last_owner(db: Session, u: User, *, losing_owner: bool) -> None:
    if not losing_owner or "owner" not in _roles(db, u.id):
        return
    owners = db.execute(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_name == "owner", User.is_active.is_(True), User.id != u.id)
    ).all()
    if not owners:
        raise HTTPException(status.HTTP_409_CONFLICT, "não é possível remover o último proprietário ativo")


def _audit(db: Session, p: Principal, action: str, u: User, payload: dict | None = None) -> None:
    audit.record(
        db,
        actor_type="user",
        actor_id=str(p.user_id),
        action=action,
        subject_type="user",
        subject_id=str(u.id),
        payload=payload or {},
    )


@router.get("")
def list_users(db: Session = Depends(get_db), _: Principal = Depends(require("users:manage"))):
    users = db.execute(select(User).order_by(User.created_at)).scalars()
    return {"items": [_out(db, u) for u in users], "roles": {k: v[0] for k, v in ROLES.items()}}


class UserIn(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=2, max_length=120)
    roles: list[str] = Field(min_length=1)


@router.post("", status_code=status.HTTP_201_CREATED)
def add_user(body: UserIn, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))):
    """Cria o usuário com senha temporária (mostrada uma única vez). MFA é cadastrado no primeiro login."""
    roles = _check_grant(body.roles, p)
    if db.execute(select(User.id).where(User.email == body.email.lower())).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "e-mail já cadastrado")
    temp = secrets.token_urlsafe(15)
    u = create_user(db, email=body.email, display_name=body.display_name, password=temp, roles=sorted(roles))
    _audit(db, p, "user.created", u, {"roles": sorted(roles)})
    return {**_out(db, u), "temporary_password": temp}


class RolesIn(BaseModel):
    roles: list[str] = Field(min_length=1)


@router.put("/{user_id}/roles")
def set_roles(
    user_id: uuid.UUID, body: RolesIn, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))
):
    u = _target(db, user_id, p)
    new = _check_grant(body.roles, p)
    old = _roles(db, u.id)
    _guard_last_owner(db, u, losing_owner="owner" in old and "owner" not in new)
    for r in old - new:
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == u.id, UserRole.role_name == r))
    for r in new - old:
        db.add(UserRole(user_id=u.id, role_name=r))
    u.session_version += 1  # permissões mudaram: tokens antigos deixam de valer
    db.flush()
    _audit(db, p, "user.roles.changed", u, {"before": sorted(old), "after": sorted(new)})
    return _out(db, u)


@router.post("/{user_id}/deactivate")
def deactivate(user_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))):
    u = _target(db, user_id, p)
    _guard_last_owner(db, u, losing_owner=True)
    u.is_active = False
    u.session_version += 1
    _audit(db, p, "user.deactivated", u)
    return _out(db, u)


@router.post("/{user_id}/activate")
def activate(user_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))):
    u = _target(db, user_id, p)
    u.is_active = True
    _audit(db, p, "user.activated", u)
    return _out(db, u)


@router.post("/{user_id}/unlock")
def unlock(user_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))):
    u = _target(db, user_id, p)
    u.locked_until, u.failed_logins, u.failed_mfa_attempts = None, 0, 0
    _audit(db, p, "user.unlocked", u)
    return _out(db, u)


@router.post("/{user_id}/reset-mfa")
def reset_mfa(user_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(require("users:manage"))):
    """Celular perdido e sem códigos de recuperação: o usuário recadastra o MFA no próximo login."""
    u = _target(db, user_id, p)
    mfa.reset(db, u, actor=str(p.user_id))
    return _out(db, u)
