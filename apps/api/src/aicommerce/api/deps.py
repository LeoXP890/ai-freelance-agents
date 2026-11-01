from __future__ import annotations

import uuid
from collections.abc import Callable

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from aicommerce.db import get_db
from aicommerce.identity.security import Principal, decode_token
from aicommerce.identity.service import build_principal
from aicommerce.models import User


def request_id(request: Request) -> str:
    return request.state.request_id


def principal_from_token(db: Session, token: str) -> Principal:
    try:
        claims = decode_token(token, "access")
        user = db.get(User, uuid.UUID(claims["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token inválido") from None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuário inativo")
    if claims.get("sv", 0) != user.session_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão encerrada (senha ou MFA alterados)")
    return build_principal(db, user, mfa="otp" in claims.get("amr", []), token_id=claims.get("jti", ""))


def current_principal(db: Session = Depends(get_db), authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "autenticação necessária", headers={"WWW-Authenticate": "Bearer"}
        )
    return principal_from_token(db, authorization.split(" ", 1)[1])


def require(permission: str) -> Callable[..., Principal]:
    def _dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"permissão necessária: {permission}")
        return p

    return _dep
