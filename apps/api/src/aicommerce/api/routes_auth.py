"""Autenticação: senha → (cadastro de MFA com QR | desafio MFA) → token de acesso.

Rotas que registram falhas usam sessão própria e só levantam o erro depois do commit: tentativas
erradas precisam persistir mesmo quando a resposta é 401.
"""

from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from aicommerce.api.deps import current_principal, request_id
from aicommerce.db import session_scope
from aicommerce.governance import audit
from aicommerce.identity import devices, mfa
from aicommerce.identity.security import (
    Principal,
    decode_token,
    issue_access_token,
    issue_mfa_challenge,
    issue_mfa_enrollment,
)
from aicommerce.identity.service import AuthError, authenticate_password, roles_and_permissions
from aicommerce.identity.service import change_password as change_password_service
from aicommerce.models import User

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=256)


class MfaIn(BaseModel):
    challenge_token: str
    code: str = Field(pattern=r"^\d{6}$")


class EnrollStartIn(BaseModel):
    enrollment_token: str


class EnrollConfirmIn(BaseModel):
    enrollment_token: str
    code: str = Field(pattern=r"^\d{6}$")


def _login_succeeded(db, user: User, *, mfa_used: bool, rid: str) -> dict:
    audit.record(
        db,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.login.succeeded",
        subject_type="user",
        subject_id=str(user.id),
        payload={"mfa": mfa_used},
        request_id=rid,
    )
    return {
        "access_token": issue_access_token(user.id, mfa=mfa_used, session_version=user.session_version),
        "token_type": "bearer",
    }


def _user_from(db, token: str, expected_type: str) -> User:
    try:
        claims = decode_token(token, expected_type)
        user = db.get(User, uuid.UUID(claims["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        user = None
    if user is None or not user.is_active or claims.get("sv", 0) != user.session_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão de autenticação inválida ou expirada")
    return user


def _mfa_status(e: mfa.MfaError) -> int:
    return status.HTTP_423_LOCKED if isinstance(e, mfa.MfaLocked) else status.HTTP_401_UNAUTHORIZED


@router.post("/login")
def login(body: LoginIn, rid: str = Depends(request_id)):
    with session_scope() as db:
        try:
            user = authenticate_password(db, body.email, body.password)
        except AuthError as e:
            failure = str(e)
        else:
            _, _, requires_mfa = roles_and_permissions(db, user.id)
            if requires_mfa and mfa.needs_enrollment(user):
                return {
                    "mfa_required": True,
                    "mfa_enrollment_required": True,
                    "enrollment_token": issue_mfa_enrollment(user.id, user.session_version),
                }
            if requires_mfa or user.mfa_enabled:
                return {
                    "mfa_required": True,
                    "mfa_enrollment_required": False,
                    "challenge_token": issue_mfa_challenge(user.id, user.session_version),
                }
            return {"mfa_required": False, **_login_succeeded(db, user, mfa_used=False, rid=rid)}
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, failure)


@router.post("/mfa/verify")
def mfa_verify(body: MfaIn, rid: str = Depends(request_id)):
    with session_scope() as db:
        user = _user_from(db, body.challenge_token, "mfa_challenge")
        try:
            mfa.verify_login_code(db, user, body.code)
        except mfa.MfaError as e:
            error = e
        else:
            return _login_succeeded(db, user, mfa_used=True, rid=rid)
    raise HTTPException(_mfa_status(error), str(error))


@router.post("/mfa/enroll/start")
def mfa_enroll_start(body: EnrollStartIn):
    """Gera (ou regenera) o segredo pendente e devolve QR code + chave para digitação manual."""
    with session_scope() as db:
        user = _user_from(db, body.enrollment_token, "mfa_enroll")
        try:
            return mfa.begin_enrollment(db, user)
        except mfa.MfaError as e:
            error = e
    raise HTTPException(
        status.HTTP_423_LOCKED if isinstance(error, mfa.MfaLocked) else status.HTTP_409_CONFLICT, str(error)
    )


@router.post("/mfa/enroll/confirm")
def mfa_enroll_confirm(body: EnrollConfirmIn, rid: str = Depends(request_id)):
    with session_scope() as db:
        user = _user_from(db, body.enrollment_token, "mfa_enroll")
        try:
            mfa.confirm_enrollment(db, user, body.code)
        except mfa.MfaError as e:
            error = e
        else:
            codes = mfa.generate_recovery_codes(db, user, actor=str(user.id))
            return {**_login_succeeded(db, user, mfa_used=True, rid=rid), "recovery_codes": codes}
    raise HTTPException(_mfa_status(error), str(error))


class RecoverIn(BaseModel):
    challenge_token: str
    recovery_code: str = Field(min_length=8, max_length=32)


@router.post("/mfa/recover")
def mfa_recover(body: RecoverIn, rid: str = Depends(request_id)):
    """Login com código de recuperação (celular perdido). Consome o código; avisa quantos restam."""
    with session_scope() as db:
        user = _user_from(db, body.challenge_token, "mfa_challenge")
        try:
            left = mfa.verify_recovery_code(db, user, body.recovery_code)
        except mfa.MfaError as e:
            error = e
        else:
            return {**_login_succeeded(db, user, mfa_used=True, rid=rid), "recovery_codes_left": left}
    raise HTTPException(_mfa_status(error), str(error))


class RegenerateIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.get("/mfa/recovery-codes")
def recovery_status(p: Principal = Depends(current_principal)):
    with session_scope() as db:
        user = db.get(User, p.user_id)
        return {"mfa_enabled": user.mfa_enabled, "remaining": mfa.recovery_codes_left(db, user)}


@router.post("/mfa/recovery-codes")
def regenerate_recovery(body: RegenerateIn, p: Principal = Depends(current_principal)):
    """Gera novos códigos (invalida os antigos). Exige o código TOTP atual."""
    with session_scope() as db:
        user = db.get(User, p.user_id, with_for_update=True)
        try:
            mfa.verify_login_code(db, user, body.code)
            return {"recovery_codes": mfa.generate_recovery_codes(db, user, actor=str(user.id))}
        except mfa.MfaError as e:
            error = e
    raise HTTPException(_mfa_status(error), str(error))


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/password")
def change_password(body: PasswordIn, p: Principal = Depends(current_principal)):
    """Troca a senha e encerra todas as outras sessões; devolve um token novo para esta."""
    with session_scope() as db:
        user = db.get(User, p.user_id, with_for_update=True)
        try:
            change_password_service(db, user, body.current_password, body.new_password)
        except AuthError as e:
            failure = str(e)
        else:
            return {
                "access_token": issue_access_token(user.id, mfa=p.mfa, session_version=user.session_version),
                "token_type": "bearer",
            }
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, failure)


@router.get("/me")
def me(p: Principal = Depends(current_principal)):
    return {
        "user_id": str(p.user_id),
        "email": p.email,
        "roles": sorted(p.roles),
        "permissions": sorted(p.permissions),
        "mfa": p.mfa,
        "simulated": p.simulated,
    }


# ───────────── Computadores lembrados (app desktop abre sem login após a 1ª vez) ─────────────
class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DeviceLoginIn(BaseModel):
    device_token: str = Field(min_length=20, max_length=200)


def _device_out(d) -> dict:
    return {
        "id": str(d.id),
        "name": d.name,
        "created_at": d.created_at.isoformat(),
        "last_used_at": d.last_used_at.isoformat() if d.last_used_at else None,
        "revoked": d.revoked_at is not None,
    }


@router.post("/devices", status_code=status.HTTP_201_CREATED)
def remember_device(body: DeviceIn, p: Principal = Depends(current_principal)):
    """Lembra este computador: devolve o segredo UMA vez (o app guarda no cofre do SO, nunca em localStorage)."""
    with session_scope() as db:
        user = db.get(User, p.user_id, with_for_update=True)
        try:
            dev, token = devices.create(db, user, mfa=p.mfa, name=body.name)
        except devices.DeviceError as e:
            error = e
        else:
            return {**_device_out(dev), "device_token": token}
    raise HTTPException(error.http, str(error))


@router.post("/device-login")
def device_login(body: DeviceLoginIn, rid: str = Depends(request_id)):
    with session_scope() as db:
        try:
            user, dev = devices.login(db, body.device_token)
        except devices.DeviceError as e:
            error = e
        else:
            return {
                "access_token": issue_access_token(user.id, mfa=dev.mfa, session_version=user.session_version),
                "token_type": "bearer",
                "device_id": str(dev.id),
            }
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error))


@router.get("/devices")
def my_devices(p: Principal = Depends(current_principal)):
    with session_scope() as db:
        return {"items": [_device_out(d) for d in devices.list_for(db, p.user_id)]}


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: uuid.UUID, p: Principal = Depends(current_principal)):
    with session_scope() as db:
        try:
            return _device_out(devices.revoke(db, p.user_id, device_id, actor=str(p.user_id)))
        except devices.DeviceError as e:
            error = e
    raise HTTPException(error.http, str(error))
