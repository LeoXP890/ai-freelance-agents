from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.governance import audit
from aicommerce.identity import pii
from aicommerce.identity.security import Principal, hash_password, verify_password
from aicommerce.models import Role, RolePermission, User, UserRole

MAX_FAILED_LOGINS = 5
LOCKOUT = dt.timedelta(minutes=15)


class AuthError(Exception):
    pass


def roles_and_permissions(db: Session, user_id: uuid.UUID) -> tuple[set[str], set[str], bool]:
    rows = db.execute(
        select(Role.name, Role.requires_mfa, RolePermission.permission_code)
        .join(UserRole, UserRole.role_name == Role.name)
        .outerjoin(RolePermission, RolePermission.role_name == Role.name)
        .where(UserRole.user_id == user_id)
    ).all()
    roles = {r.name for r in rows}
    perms = {r.permission_code for r in rows if r.permission_code}
    return roles, perms, any(r.requires_mfa for r in rows)


def build_principal(db: Session, user: User, mfa: bool, token_id: str = "") -> Principal:
    roles, perms, requires_mfa = roles_and_permissions(db, user.id)
    if requires_mfa and not mfa:
        # Sessão sem segundo fator não recebe NENHUMA permissão de papéis que exigem MFA.
        perms = set()
    return Principal(user.id, user.email, frozenset(roles), frozenset(perms), mfa, user.is_simulated, token_id)


def authenticate_password(db: Session, email: str, password: str) -> User:
    user = db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()
    now = dt.datetime.now(dt.UTC)
    if user and user.locked_until and user.locked_until > now:
        verify_password(None, password)
        raise AuthError("conta temporariamente bloqueada")
    if not verify_password(user.password_hash if user else None, password) or not user or not user.is_active:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.locked_until = now + LOCKOUT
                user.failed_logins = 0
            audit.record(
                db,
                actor_type="user",
                actor_id=str(user.id),
                action="auth.login.failed",
                subject_type="user",
                subject_id=str(user.id),
            )
        raise AuthError("credenciais inválidas")
    user.failed_logins = 0
    user.locked_until = None
    return user


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    roles: list[str],
    totp_secret: str | None = None,
    simulated: bool = False,
) -> User:
    user = User(
        email=email.lower().strip(),
        display_name=display_name,
        password_hash=hash_password(password),
        totp_secret=pii.seal(totp_secret),
        mfa_enabled=totp_secret is not None,
        is_simulated=simulated,
    )
    db.add(user)
    db.flush()
    for role in roles:
        db.add(UserRole(user_id=user.id, role_name=role))
    db.flush()
    return user


MIN_PASSWORD = 12


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(user.password_hash, current):
        raise AuthError("senha atual incorreta")
    if len(new) < MIN_PASSWORD:
        raise AuthError(f"a nova senha precisa de ao menos {MIN_PASSWORD} caracteres")
    if new == current:
        raise AuthError("a nova senha deve ser diferente da atual")
    if new.lower() in user.email.lower() or user.email.split("@")[0].lower() in new.lower():
        raise AuthError("a senha não pode conter o e-mail")
    user.password_hash = hash_password(new)
    user.session_version += 1  # invalida todas as sessões/tokens emitidos antes da troca
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.password.changed",
        subject_type="user",
        subject_id=str(user.id),
    )
