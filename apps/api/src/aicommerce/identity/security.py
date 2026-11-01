"""Senhas (argon2id), tokens JWT de acesso e desafio MFA (TOTP)."""

from __future__ import annotations

import datetime as dt
import hmac
import uuid
from dataclasses import dataclass, field

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from aicommerce.config import get_settings

_hasher = PasswordHasher()
# Hash fixo usado quando o usuário não existe: iguala o tempo de resposta (sem enumeração de e-mails).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(stored_hash: str | None, raw: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, raw) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def matching_totp_counter(secret: str, code: str, at: dt.datetime | None = None) -> int | None:
    """Passo TOTP (contador de 30 s) que gera `code`, tolerando ±1 passo de relógio; None se inválido."""
    totp = pyotp.TOTP(secret)
    now = at or dt.datetime.now(dt.UTC)
    current = totp.timecode(now)
    for counter in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.generate_otp(counter), code):
            return counter
    return None


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    roles: frozenset[str]
    permissions: frozenset[str]
    mfa: bool
    simulated: bool
    token_id: str = field(default="")

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def _encode(claims: dict, ttl: int) -> str:
    s = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {
        **claims,
        "iss": s.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + dt.timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def issue_access_token(user_id: uuid.UUID, mfa: bool, session_version: int = 0) -> str:
    return _encode(
        {"sub": str(user_id), "typ": "access", "amr": ["pwd", "otp"] if mfa else ["pwd"], "sv": session_version},
        get_settings().access_token_ttl_seconds,
    )


def issue_mfa_challenge(user_id: uuid.UUID, session_version: int = 0) -> str:
    return _encode(
        {"sub": str(user_id), "typ": "mfa_challenge", "sv": session_version}, get_settings().mfa_challenge_ttl_seconds
    )


def issue_mfa_enrollment(user_id: uuid.UUID, session_version: int = 0) -> str:
    """Emitido só após senha correta, para usuário que exige MFA e ainda não cadastrou fator."""
    return _encode(
        {"sub": str(user_id), "typ": "mfa_enroll", "sv": session_version}, get_settings().mfa_enroll_ttl_seconds
    )


def decode_token(token: str, expected_type: str) -> dict:
    s = get_settings()
    claims = jwt.decode(
        token,
        s.jwt_secret,
        algorithms=["HS256"],
        issuer=s.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "typ"]},
    )
    if claims.get("typ") != expected_type:
        raise jwt.InvalidTokenError("tipo de token inesperado")
    return claims
