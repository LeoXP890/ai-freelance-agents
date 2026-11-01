"""Segundo fator TOTP: cadastro self-service com confirmação, verificação com limite e anti-replay.

Regras:
* Cadastro só é permitido enquanto o usuário NÃO tem fator confirmado — quem tem apenas a senha não
  consegue substituir o MFA existente de outra pessoa.
* O segredo fica pendente até um código válido ser confirmado (evita ativar MFA que o app não gera).
* Falhas de código usam contador próprio que a senha correta não zera; 5 falhas bloqueiam a conta.
* Um passo TOTP já aceito não é aceito de novo (replay).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

import pyotp
import segno
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from aicommerce.governance import audit
from aicommerce.identity import pii
from aicommerce.identity.security import matching_totp_counter, new_totp_secret
from aicommerce.identity.service import LOCKOUT, MAX_FAILED_LOGINS
from aicommerce.models import MfaRecoveryCode, User

ISSUER = "AI Commerce"


class MfaError(Exception):
    pass


class MfaLocked(MfaError):
    pass


def needs_enrollment(user: User) -> bool:
    return not (user.mfa_enabled and user.totp_secret)  # só presença: não precisa decifrar


def _check_lock(user: User, now: dt.datetime) -> None:
    if user.locked_until and user.locked_until > now:
        raise MfaLocked("conta temporariamente bloqueada")


def _fail(db: Session, user: User, now: dt.datetime, action: str) -> None:
    user.failed_mfa_attempts += 1
    if user.failed_mfa_attempts >= MAX_FAILED_LOGINS:
        user.locked_until = now + LOCKOUT
        user.failed_mfa_attempts = 0
    audit.record(
        db, actor_type="user", actor_id=str(user.id), action=action, subject_type="user", subject_id=str(user.id)
    )


def begin_enrollment(db: Session, user: User) -> dict[str, str]:
    now = dt.datetime.now(dt.UTC)
    _check_lock(user, now)
    if not needs_enrollment(user):
        raise MfaError("MFA já cadastrado; redefinição exige administrador")
    secret = new_totp_secret()
    user.totp_pending_secret = pii.seal(secret)  # segredo do MFA nunca em claro no banco
    uri = pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name=ISSUER)
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.mfa.enrollment.started",
        subject_type="user",
        subject_id=str(user.id),
    )
    qr = segno.make(uri, error="m").svg_data_uri(scale=6, border=2, dark="#0b1020", light="#ffffff")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg_data_uri": qr, "issuer": ISSUER, "account": user.email}


def confirm_enrollment(db: Session, user: User, code: str) -> None:
    now = dt.datetime.now(dt.UTC)
    _check_lock(user, now)
    if not needs_enrollment(user):
        raise MfaError("MFA já cadastrado")
    if not user.totp_pending_secret:
        raise MfaError("cadastro não iniciado")
    counter = matching_totp_counter(pii.unseal(user.totp_pending_secret) or "", code, now)
    if counter is None:
        _fail(db, user, now, "auth.mfa.enrollment.failed")
        raise MfaError("código inválido — confira o horário do celular e tente o código atual")
    user.totp_secret, user.totp_pending_secret = user.totp_pending_secret, None
    user.mfa_enabled = True
    user.totp_last_counter = counter
    user.failed_mfa_attempts = 0
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.mfa.enrolled",
        subject_type="user",
        subject_id=str(user.id),
    )


def verify_login_code(db: Session, user: User, code: str) -> None:
    now = dt.datetime.now(dt.UTC)
    _check_lock(user, now)
    if needs_enrollment(user):
        raise MfaError("MFA não cadastrado")
    counter = matching_totp_counter(pii.unseal(user.totp_secret) or "", code, now)
    if counter is None:
        _fail(db, user, now, "auth.mfa.failed")
        raise MfaError("código MFA inválido")
    if user.totp_last_counter is not None and counter <= user.totp_last_counter:
        _fail(db, user, now, "auth.mfa.replay_rejected")
        raise MfaError("código já utilizado — aguarde o próximo")
    user.totp_last_counter = counter
    user.failed_mfa_attempts = 0


RECOVERY_COUNT = 10
_B32 = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem I/O/0/1: menos erro de digitação


def _recovery_hash(code: str) -> str:
    norm = "".join(ch for ch in code.upper() if ch.isalnum())
    return hashlib.sha256(norm.encode()).hexdigest()


def generate_recovery_codes(db: Session, user: User, *, actor: str) -> list[str]:
    """Gera 10 códigos novos (80 bits cada) e invalida os anteriores. Os códigos só aparecem nesta resposta."""
    if not user.mfa_enabled:
        raise MfaError("MFA não cadastrado")
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(_B32) for _ in range(16))
        codes.append("-".join(raw[i : i + 4] for i in range(0, 16, 4)))
        db.add(MfaRecoveryCode(user_id=user.id, code_sha256=_recovery_hash(raw)))
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="auth.mfa.recovery.generated",
        subject_type="user",
        subject_id=str(user.id),
        payload={"count": RECOVERY_COUNT},
    )
    return codes


def recovery_codes_left(db: Session, user: User) -> int:
    return db.execute(
        select(func.count()).where(MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.used_at.is_(None))
    ).scalar_one()


def verify_recovery_code(db: Session, user: User, code: str) -> int:
    """Segundo fator alternativo (celular perdido). Uso único; falhas contam no mesmo limite do TOTP."""
    now = dt.datetime.now(dt.UTC)
    _check_lock(user, now)
    if needs_enrollment(user):
        raise MfaError("MFA não cadastrado")
    row = db.execute(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_sha256 == _recovery_hash(code),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        _fail(db, user, now, "auth.mfa.recovery.failed")
        raise MfaError("código de recuperação inválido ou já usado")
    row.used_at = now
    user.failed_mfa_attempts = 0
    db.flush()
    left = recovery_codes_left(db, user)
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.mfa.recovery.used",
        subject_type="user",
        subject_id=str(user.id),
        payload={"remaining": left},
    )
    return left


def reset(db: Session, user: User, *, actor: str) -> None:
    """Remove o fator: no próximo login o usuário passa pelo cadastro com QR."""
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_last_counter = None
    user.mfa_enabled = False
    user.failed_mfa_attempts = 0
    user.session_version += 1  # sessões abertas com o fator antigo deixam de valer
    audit.record(
        db, actor_type="system", actor_id=actor, action="user.mfa.reset", subject_type="user", subject_id=str(user.id)
    )
