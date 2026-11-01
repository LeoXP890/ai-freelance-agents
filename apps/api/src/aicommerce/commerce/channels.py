"""Contas de canal (marketplaces): conexão OAuth, tokens cifrados e renovação antes do uso.

DRY_RUN usa o servidor OAuth simulado (access 6 h, refresh de uso único). Refresh inválido → conta
REAUTH_REQUIRED e a execução falha com TOKEN_EXPIRED (erro permanente; exige reconexão humana).

LIVE (Mercado Livre): o proprietário inicia a conexão no app (`oauth_start`), autoriza no site do ML e o ML redireciona
para o callback (`oauth_finish`), que troca o código pelos tokens com PKCE. O verificador nunca sai do servidor;
o state é de uso único e expira em 10 min. A renovação usa o endpoint real e é serializada pela trava da conta
(o refresh token do ML é de uso único).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.config import get_settings
from aicommerce.governance import audit
from aicommerce.identity import pii
from aicommerce.models import ChannelAccount, OAuthState
from aicommerce.providers.marketplaces import ML_TOKEN_URL, MOCK_ML_OAUTH, ml_authorize_url, pkce_pair
from aicommerce.providers.woocommerce import _http

MARKETPLACES = ("MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP")
REFRESH_MARGIN = dt.timedelta(minutes=10)


OAUTH_TTL = dt.timedelta(minutes=10)


class TokenUnavailable(Exception):
    transient = False


class TokenRefreshTransient(TokenUnavailable):
    """ML fora do ar/limite na renovação: tentar de novo depois (a conta continua conectada)."""

    transient = True


class OAuthError(Exception):
    pass


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _real_refresh(refresh_token: str, now: dt.datetime) -> dict:
    """Renova no endpoint real do ML. invalid_grant → PermissionError (reconectar); falha de rede/5xx → transitória."""
    s = get_settings()
    secret = s.ml_client_secret.get_secret_value() if s.ml_client_secret else ""
    body = {
        "grant_type": "refresh_token",
        "client_id": s.ml_application_id,
        "client_secret": secret,
        "refresh_token": refresh_token,
    }
    try:
        status, data = _http(
            "POST",
            ML_TOKEN_URL,
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            body,
            30.0,
            form=True,
        )
    except (TimeoutError, OSError) as e:
        raise TokenRefreshTransient(f"Mercado Livre indisponível na renovação do token ({type(e).__name__})") from e
    if status == 200:
        return _token_fields(data, now)
    if status in (408, 429) or status >= 500:
        raise TokenRefreshTransient(f"Mercado Livre indisponível na renovação do token (HTTP {status})")
    raise PermissionError(f"{(data or {}).get('error', 'invalid_grant')}: renovação recusada (HTTP {status})")


def _token_fields(data: dict, now: dt.datetime) -> dict:
    return {
        "access_token": str(data["access_token"]),
        "refresh_token": str(data["refresh_token"]),
        "expires_at": now + dt.timedelta(seconds=int(data.get("expires_in") or 21600)),
        "user_id": str(data.get("user_id") or ""),
        "scope": str(data.get("scope") or ""),
    }


def oauth_start(db: Session, channel: str, *, actor: str, now: dt.datetime | None = None) -> str:
    """Inicia a conexão REAL: guarda state (hash) + verificador PKCE (cifrado) e devolve a URL de autorização do ML."""
    s = get_settings()
    if channel != "MERCADO_LIVRE":
        raise OAuthError(f"conexão real de {channel} ainda não disponível (contrato não verificado)")
    if s.is_dry_run:
        raise OAuthError("conexão real só em LIVE; em DRY_RUN use a conexão simulada")
    if not (
        s.ml_client_secret and s.ml_redirect_uri and s.ml_application_id and not s.ml_application_id.startswith("SIM")
    ):
        raise OAuthError(
            "aplicação do Mercado Livre não configurada (AIC_ML_APPLICATION_ID/CLIENT_SECRET/REDIRECT_URI)"
        )
    now = now or dt.datetime.now(dt.UTC)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    db.add(
        OAuthState(
            state_sha256=_sha(state),
            channel=channel,
            code_verifier_ciphertext=_enc(verifier),
            redirect_uri=s.ml_redirect_uri,
            created_by=actor,
            expires_at=now + OAUTH_TTL,
        )
    )
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="channel.oauth.started",
        subject_type="channel",
        subject_id=channel,
        payload={"redirect_uri": s.ml_redirect_uri},
    )
    return ml_authorize_url(s.ml_application_id, s.ml_redirect_uri, state, challenge)


def oauth_finish(db: Session, *, code: str, state: str, now: dt.datetime | None = None) -> ChannelAccount:
    """Callback do ML: valida o state (uso único, não expirado), troca o código com PKCE e guarda os tokens cifrados."""
    now = now or dt.datetime.now(dt.UTC)
    row = db.get(OAuthState, _sha(state), with_for_update=True)
    if row is None:
        raise OAuthError("state desconhecido ou já usado — inicie a conexão de novo pelo app")
    db.delete(row)  # uso único, mesmo se a troca falhar
    db.flush()
    if row.expires_at <= now:
        raise OAuthError("a autorização demorou mais de 10 minutos — inicie a conexão de novo pelo app")
    s = get_settings()
    body = {
        "grant_type": "authorization_code",
        "client_id": s.ml_application_id,
        "client_secret": s.ml_client_secret.get_secret_value() if s.ml_client_secret else "",
        "code": code,
        "redirect_uri": row.redirect_uri,
        "code_verifier": _dec(row.code_verifier_ciphertext) or "",
    }
    try:
        status, data = _http(
            "POST",
            ML_TOKEN_URL,
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            body,
            30.0,
            form=True,
        )
    except (TimeoutError, OSError) as e:
        raise OAuthError(f"Mercado Livre não respondeu ({type(e).__name__}); tente de novo") from e
    if status != 200 or not isinstance(data, dict) or "access_token" not in data:
        err = data.get("error") if isinstance(data, dict) else None
        raise OAuthError(f"Mercado Livre recusou a autorização (HTTP {status}: {err or 'sem detalhe'})")
    tok = _token_fields(data, now)
    acc = (
        db.execute(
            select(ChannelAccount).where(
                ChannelAccount.channel == row.channel, ChannelAccount.external_account_id == tok["user_id"]
            )
        )
        .scalars()
        .first()
    )
    if acc is None:
        acc = ChannelAccount(channel=row.channel, external_account_id=tok["user_id"], is_simulated=False, scopes=[])
        db.add(acc)
    # uma conta real conectada por canal: outras contas reais do mesmo canal deixam de ser usadas
    for other in db.execute(
        select(ChannelAccount).where(
            ChannelAccount.channel == row.channel,
            ChannelAccount.is_simulated.is_(False),
            ChannelAccount.external_account_id != tok["user_id"],
            ChannelAccount.status == "CONNECTED",
        )
    ).scalars():
        other.status = "REPLACED"
    acc.status, acc.expires_at, acc.scopes = "CONNECTED", tok["expires_at"], tok["scope"].split()
    acc.access_token_ciphertext, acc.refresh_token_ciphertext = _enc(tok["access_token"]), _enc(tok["refresh_token"])
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=row.created_by,
        action="channel.account.connected",
        subject_type="channel",
        subject_id=row.channel,
        payload={"external_account_id": acc.external_account_id, "simulated": False},
    )
    return acc


def live_token(channel: str) -> tuple[str, str]:
    """Para o cliente real: (access token válido, id do vendedor), renovando se preciso. Sessão própria."""
    from aicommerce.db import session_scope

    failure = None
    with session_scope() as db:
        try:
            token = valid_access_token(db, channel)
        except TokenUnavailable as exc:
            # Commit REAUTH_REQUIRED and its audit event before propagating the typed failure.
            # Raising inside session_scope rolled both back and retried the revoked refresh token.
            failure = exc
        else:
            acc = _account(db, channel)
            result = token, acc.external_account_id if acc else ""
    if failure is not None:
        raise failure
    return result


def _account(db: Session, channel: str, *, lock: bool = False) -> ChannelAccount | None:
    q = select(ChannelAccount).where(
        ChannelAccount.channel == channel,
        ChannelAccount.status == "CONNECTED",
        ChannelAccount.is_simulated.is_(get_settings().is_dry_run),  # LIVE nunca usa conta simulada (e vice-versa)
    )
    return db.execute(q.with_for_update() if lock else q).scalars().first()


def live_accounts(db: Session) -> frozenset[str]:
    """Canais com conta REAL conectada (para o registro de provedores decidir a capacidade)."""
    q = select(ChannelAccount.channel).where(
        ChannelAccount.status == "CONNECTED", ChannelAccount.is_simulated.is_(False)
    )
    return frozenset(db.execute(q).scalars())


def _enc(v: str) -> str:
    return pii.encrypt({"v": v})


def _dec(v: str | None) -> str | None:
    return pii.decrypt(v)["v"] if v else None


def connect_simulated(db: Session, channel: str, *, actor: str) -> ChannelAccount:
    if not get_settings().is_dry_run:
        raise TokenUnavailable("conexão simulada só existe em DRY_RUN")
    tok = MOCK_ML_OAUTH.issue()
    acc = db.execute(select(ChannelAccount).where(ChannelAccount.channel == channel)).scalars().first()
    if acc is None:
        acc = ChannelAccount(
            channel=channel, external_account_id=tok["user_id"], is_simulated=True, scopes=["read", "write"]
        )
        db.add(acc)
    acc.status, acc.expires_at = "CONNECTED", tok["expires_at"]
    acc.access_token_ciphertext, acc.refresh_token_ciphertext = _enc(tok["access_token"]), _enc(tok["refresh_token"])
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="channel.account.connected",
        subject_type="channel",
        subject_id=channel,
        payload={"external_account_id": acc.external_account_id, "simulated": True},
    )
    return acc


def valid_access_token(db: Session, channel: str, now: dt.datetime | None = None) -> str:
    """Token válido para o canal, renovando se estiver perto de expirar. Nunca registra o token em log."""
    now = now or dt.datetime.now(dt.UTC)
    acc = _account(db, channel, lock=True)
    if acc is None:
        raise TokenUnavailable(f"nenhuma conta {channel} conectada")
    if acc.expires_at and acc.expires_at - REFRESH_MARGIN > now:
        return _dec(acc.access_token_ciphertext) or ""
    try:
        refresh = _dec(acc.refresh_token_ciphertext) or ""
        tok = MOCK_ML_OAUTH.refresh(refresh, now) if acc.is_simulated else _real_refresh(refresh, now)
    except PermissionError as e:
        acc.status = "REAUTH_REQUIRED"
        audit.record(
            db,
            actor_type="system",
            actor_id="oauth",
            action="channel.account.reauth_required",
            subject_type="channel",
            subject_id=channel,
            payload={"reason": str(e)},
        )
        raise TokenUnavailable(f"token expirado e renovação recusada ({e}); reconecte a conta") from e
    acc.access_token_ciphertext, acc.refresh_token_ciphertext = _enc(tok["access_token"]), _enc(tok["refresh_token"])
    acc.expires_at = tok["expires_at"]
    audit.record(
        db,
        actor_type="system",
        actor_id="oauth",
        action="channel.account.token_refreshed",
        subject_type="channel",
        subject_id=channel,
        payload={"expires_at": acc.expires_at.isoformat()},
    )
    return tok["access_token"]
