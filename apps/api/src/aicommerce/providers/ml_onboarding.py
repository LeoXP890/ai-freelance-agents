"""Operator-only OAuth diagnostics, isolated from the application and its commercial credentials.

No ChannelAccount, application settings, LIVE flag or commerce provider is touched. Network I/O is
restricted to the fixed OAuth token endpoint and MLReadOnly GETs. macOS/Linux private local vault;
the local key is not a substitute for an external secret manager or an offsite recovery plan.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aicommerce.providers.marketplaces import ML_TOKEN_URL, ml_authorize_url, pkce_pair
from aicommerce.providers.ml_readonly import MLReadOnly
from aicommerce.providers.woocommerce import _http

AAD = b"aicommerce-ml-readonly-vault-v1"
TTL = 600


class OnboardingError(Exception):
    """Only fixed, non-secret messages cross the CLI boundary."""


def redirect_uri(value: str) -> str:
    import ipaddress

    try:
        u = urlsplit(value)
        host = u.hostname or ""
        invalid = (
            u.scheme != "https"
            or not host.isascii()
            or "." not in host
            or host.endswith((".localhost", ".local", ".invalid", ".test"))
            or u.username is not None
            or u.password is not None
            or u.port not in (None, 443)
            or u.query
            or u.fragment
            or any(c.isspace() for c in value)
            or "%" in u.path
            or any(p in (".", "..") for p in u.path.split("/"))
        )
        try:
            ipaddress.ip_address(host)
            invalid = True
        except ValueError:
            pass
    except ValueError:
        invalid = True
    if invalid:
        raise OnboardingError("REDIRECT_REQUIRES_FIXED_HTTPS_DOMAIN")
    return value


def private_read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        s = os.fstat(stream.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_size > 65536:
            raise OnboardingError("PRIVATE_FILE_REQUIRED")
        return stream.read()


def _atomic(path: Path, data: bytes):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


_HINTS = (
    ("REDIRECT_URI_MISMATCH", ("redirect",)),
    ("PKCE_MISMATCH", ("verifier", "challenge", "pkce")),
    ("CODE_EXPIRED", ("expired", "expir")),
    ("CODE_ALREADY_USED", ("already used", "used")),
    ("CLIENT_MISMATCH", ("client",)),
)


def _exchange_hint(reply) -> str:
    """Classifica a descrição remota em categoria fixa; o texto em si nunca é guardado nem exibido."""
    if not isinstance(reply, dict):
        return "NONE"
    text = " ".join(str(reply.get(k, "")) for k in ("error_description", "message")).lower()[:2000]
    if not text.strip():
        return "NONE"
    return next((hint for hint, words in _HINTS if any(w in text for w in words)), "OTHER")


# Permissão padrão "Usuários": o DevCenter a fixa em leitura e escrita e o token traz estes três em read-write.
PLATFORM_USER_SCOPES = frozenset({"urn:global:admin:info", "urn:global:admin:oauth", "urn:global:admin:users"})
_ML_READ_ONLY = re.compile(r"urn:ml:[a-z0-9-]+:[a-z0-9-]+:/read-only")


def _scope_allowed(scope: str, accept_user_write: bool) -> bool:
    """Marketplace (urn:ml:*) só em read-only; read-write apenas na permissão padrão da plataforma e com opt-in."""
    if scope in ("read", "offline_access") or _ML_READ_ONLY.fullmatch(scope):
        return True
    base, _, mode = scope.partition(":/")
    if base in PLATFORM_USER_SCOPES:
        return mode == "read-only" or (mode == "read-write" and accept_user_write)
    return scope == "write" and accept_user_write


def _safe_scopes(scopes) -> list[str]:
    return sorted(x if re.fullmatch(r"[A-Za-z0-9:/._-]{1,100}", x) else "<unrecognized>" for x in scopes)[:40]


def _token_rejection(reply, accept_user_write: bool, expected_user: str | None) -> tuple[str | None, set[str]]:
    """Primeira regra violada pela resposta de token (None = aceito) e os escopos lidos."""
    if not isinstance(reply, dict):
        return "NOT_AN_OBJECT", set()
    raw_scope = reply.get("scope")
    scopes = set(raw_scope.split()) if isinstance(raw_scope, str) else set()
    access, refresh = reply.get("access_token"), reply.get("refresh_token")
    if not all(isinstance(v, str) and v and not any(c.isspace() for c in v) for v in (access, refresh)):
        return "TOKEN_FORMAT", scopes
    uid = str(reply.get("user_id", ""))
    if not uid.isascii() or not uid.isdigit():
        return "USER_ID", scopes
    expiry = reply.get("expires_in")
    if type(expiry) is not int or not 0 < expiry <= 86400:
        return "EXPIRES_IN", scopes
    if str(reply.get("token_type", "")).lower() != "bearer":
        return "TOKEN_TYPE", scopes
    if not isinstance(raw_scope, str) or "read" not in scopes:
        return "SCOPE_MISSING_READ", scopes
    if any(not _scope_allowed(x, accept_user_write) for x in scopes):
        return "SCOPE_UNEXPECTED", scopes
    if expected_user is not None and uid != expected_user:
        return "USER_MISMATCH", scopes
    return None, scopes


class ReadOnlyOnboarding:
    def __init__(self, directory: Path, *, exchange=_http, reader=MLReadOnly, clock=time.time):
        self.directory = directory.absolute()
        self.exchange, self.reader, self.clock = exchange, reader, clock

    @contextmanager
    def _locked(self, *, create=False):
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        s = self.directory.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise OnboardingError("PRIVATE_DIRECTORY_REQUIRED")
        fd = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            lock = os.fstat(stream.fileno())
            if not stat.S_ISREG(lock.st_mode) or lock.st_uid != os.getuid() or lock.st_mode & 0o077:
                raise OnboardingError("PRIVATE_FILE_REQUIRED")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _load(self):
        raw = private_read(self.directory / "vault.enc")
        return json.loads(AESGCM(private_read(self.directory / "vault.key")).decrypt(raw[:12], raw[12:], AAD))

    def _save(self, value):
        nonce = secrets.token_bytes(12)
        raw = AESGCM(private_read(self.directory / "vault.key")).encrypt(nonce, json.dumps(value).encode(), AAD)
        _atomic(self.directory / "vault.enc", nonce + raw)

    def configure(
        self, application_id: str, client_secret: str, callback: str, *, accept_platform_user_write: bool = False
    ):
        """accept_platform_user_write: o DevCenter fixa a permissão padrão "Usuários" em leitura e escrita, então o
        token pode trazer o escopo `write`. Só é aceito com esta escolha explícita do titular na configuração; o
        cliente continua emitindo apenas GET e as demais permissões da aplicação devem ficar em leitura/sem acesso."""
        callback = redirect_uri(callback)
        if not application_id.isascii() or not application_id.isdigit() or not client_secret.strip():
            raise OnboardingError("APPLICATION_CONFIGURATION_REQUIRED")
        with self._locked(create=True):
            if (self.directory / "vault.enc").exists():
                raise OnboardingError("ALREADY_CONFIGURED_CREDENTIALS_PRESERVED")
            key = self.directory / "vault.key"
            if not key.exists():
                fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            self._save(
                {
                    "application_id": application_id,
                    "client_secret": client_secret,
                    "redirect_uri": callback,
                    "accept_platform_user_write": bool(accept_platform_user_write),
                    "state": "CONFIGURADO_SEM_OAUTH",
                }
            )
        return self.status()

    @staticmethod
    def _receipt(data):
        return {
            "state": data["state"],
            "account_id": "***" + data["user_id"][-3:] if data.get("user_id") else None,
            "token_scopes": data.get("token_scopes"),
            "last_exchange_error": data.get("last_exchange_error"),
            "last_token_rejection": data.get("last_token_rejection"),
            "commercial_capabilities": False,
            "CH_ML_approved": False,
            "application_configuration_changed": False,
        }

    def status(self):
        if not self.directory.exists():
            return self._receipt({"state": "PRONTO_PARA_CADASTRO"})
        with self._locked():
            data = self._load()
            if data["state"] in ("EXCHANGE_IN_FLIGHT", "REFRESH_IN_FLIGHT"):
                data["state"] = "REAUTH_REQUIRED_UNCERTAIN_EXCHANGE"
            elif data["state"] == "OAUTH_CONECTADO" and data.get("expires_at", 0) <= self.clock():
                data["state"] = "TOKEN_EXPIRED"
            return self._receipt(data)

    def start(self) -> str:
        with self._locked():
            data = self._load()
            state = secrets.token_urlsafe(32)
            verifier, challenge = pkce_pair()
            # Reauthorization invalidates the previous local token. It does not revoke/change the seller account.
            for key in (
                "access_token",
                "refresh_token",
                "expires_at",
                "user_id",
                "last_exchange_error",
                "last_token_rejection",
            ):
                data.pop(key, None)
            data.update(
                state="AWAITING_OWNER_CONSENT",
                pending={
                    "state_hash": hashlib.sha256(state.encode()).hexdigest(),
                    "verifier": verifier,
                    "expires_at": self.clock() + TTL,
                },
            )
            self._save(data)
            return ml_authorize_url(data["application_id"], data["redirect_uri"], state, challenge)

    def _exchange(self, data, fields, expected_user=None):
        # State/refresh consumption must already be durable. Never automatically retry a one-use grant.
        try:
            status, reply = self.exchange(
                "POST",
                ML_TOKEN_URL,
                {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                {"client_id": data["application_id"], "client_secret": data["client_secret"], **fields},
                15.0,
                form=True,
            )
        except (OSError, ValueError, TypeError):
            data["state"] = "REAUTH_REQUIRED_UNCERTAIN_EXCHANGE"
            self._save(data)
            raise OnboardingError(data["state"]) from None
        if status != 200:
            data["state"] = "REAUTH_REQUIRED" if status in (400, 401, 403) else "REAUTH_REQUIRED_UNCERTAIN_EXCHANGE"
            # Só o código OAuth padronizado (ex.: invalid_grant, invalid_client) e o HTTP: nada da mensagem/corpo.
            code = reply.get("error") if isinstance(reply, dict) else None
            data["last_exchange_error"] = {
                "http": status,
                "error": code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code) else "unrecognized",
                "hint": _exchange_hint(reply),
            }
            self._save(data)
            raise OnboardingError(data["state"])
        reason, scopes = _token_rejection(reply, data.get("accept_platform_user_write", False), expected_user)
        if reason:
            data["state"] = "REAUTH_REQUIRED_INVALID_TOKEN_OR_SCOPE"
            # Diagnóstico sem segredo: regra que falhou + nomes de escopo (públicos); tokens são descartados.
            data["last_token_rejection"] = {"reason": reason, "scopes": _safe_scopes(scopes)}
            self._save(data)
            raise OnboardingError(data["state"])
        access, refresh, uid, expiry = (
            reply["access_token"],
            reply["refresh_token"],
            str(reply["user_id"]),
            reply["expires_in"],
        )
        data.pop("last_token_rejection", None)
        data.update(
            state="OAUTH_CONECTADO",
            access_token=access,
            refresh_token=refresh,
            expires_at=self.clock() + expiry,
            user_id=uid,
            token_scopes=sorted(scopes),
        )
        self._save(data)
        return self._receipt(data)

    def finish(self, callback_url: str, *, authorized=False):
        if not authorized:
            raise OnboardingError("OWNER_CONSENT_REQUIRED")
        if len(callback_url) > 16384:
            raise OnboardingError("INVALID_CALLBACK_PARAMETERS")
        with self._locked():
            data = self._load()
            pending = data.get("pending")
            if not pending or data["state"] != "AWAITING_OWNER_CONSENT":
                raise OnboardingError("STATE_UNKNOWN_OR_ALREADY_USED")
            original, callback = urlsplit(data["redirect_uri"]), urlsplit(callback_url)
            if (callback.scheme, callback.netloc, callback.path) != (
                original.scheme,
                original.netloc,
                original.path,
            ) or callback.fragment:
                raise OnboardingError("CALLBACK_URI_MISMATCH")
            query = parse_qs(callback.query, keep_blank_values=True, max_num_fields=10)
            if any(len(v) != 1 for v in query.values()) or set(query) - {
                "code",
                "state",
                "error",
                "error_description",
                "error_uri",
            }:
                raise OnboardingError("INVALID_CALLBACK_PARAMETERS")
            supplied = hashlib.sha256(query.get("state", [""])[0].encode()).hexdigest()
            if not hmac.compare_digest(supplied, pending["state_hash"]):
                raise OnboardingError("STATE_MISMATCH")
            data.pop("pending")
            data["state"] = "EXCHANGE_IN_FLIGHT"
            if pending["expires_at"] <= self.clock():
                data["state"] = "STATE_EXPIRED"
            elif "error" in query:
                data["state"] = "OWNER_DENIED"
            elif not query.get("code", [""])[0]:
                data["state"] = "INVALID_CALLBACK_PARAMETERS"
            self._save(data)  # consume before HTTP, even if it times out or this process crashes
            if data["state"] != "EXCHANGE_IN_FLIGHT":
                raise OnboardingError(data["state"])
            return self._exchange(
                data,
                {
                    "grant_type": "authorization_code",
                    "code": query["code"][0],
                    "redirect_uri": data["redirect_uri"],
                    "code_verifier": pending["verifier"],
                },
            )

    def refresh(self, *, authorized=False):
        if not authorized:
            raise OnboardingError("OWNER_CONSENT_REQUIRED")
        with self._locked():
            data = self._load()
            if data["state"] != "OAUTH_CONECTADO":
                raise OnboardingError("REAUTH_REQUIRED")
            if data["expires_at"] > self.clock() + 60:
                return self._receipt(data)
            token = data.pop("refresh_token")
            data.pop("access_token")
            data["state"] = "REFRESH_IN_FLIGHT"
            self._save(data)
            return self._exchange(data, {"grant_type": "refresh_token", "refresh_token": token}, data["user_id"])

    def research_reader(self, *, authorized=False, refresh_if_needed=False):
        """Leitor somente GET (lista fechada de MLReadOnly) para pesquisa de mercado; o token não sai deste objeto.
        Renova antes de expirar só se pedido explicitamente pelo chamador autorizado."""
        if not authorized:
            raise OnboardingError("OWNER_CONSENT_REQUIRED")
        with self._locked():
            data = self._load()
            fresh = data["state"] == "OAUTH_CONECTADO" and data["expires_at"] > self.clock() + 60
        if not fresh and refresh_if_needed and data["state"] == "OAUTH_CONECTADO":
            self.refresh(authorized=True)
            with self._locked():
                data = self._load()
                fresh = data["state"] == "OAUTH_CONECTADO" and data["expires_at"] > self.clock() + 60
        if not fresh:
            raise OnboardingError("VALID_READONLY_TOKEN_REQUIRED")
        return self.reader(data["access_token"])

    def check(self, *, authorized=False):
        if not authorized:
            raise OnboardingError("OWNER_CONSENT_REQUIRED")
        with self._locked():
            data = self._load()
            if data["state"] != "OAUTH_CONECTADO" or data["expires_at"] <= self.clock():
                raise OnboardingError("VALID_READONLY_TOKEN_REQUIRED")
            # No implicit refresh or write. Account matching happens on the unredacted in-memory result.
            result = self.reader(data["access_token"]).get("/users/me")
            matched = (
                result.status == "OK"
                and isinstance(result.data, dict)
                and str(result.data.get("id")) == data["user_id"]
            )
            if result.status in ("REAUTH_REQUIRED", "PERMISSION_DENIED") or (result.status == "OK" and not matched):
                data["state"] = "ACCOUNT_MISMATCH" if result.status == "OK" else result.status
                self._save(data)
            return {
                **self._receipt(data),
                "state": "READ_ONLY_VERIFICADO"
                if matched
                else "ACCOUNT_MISMATCH"
                if result.status == "OK"
                else result.status,
                "http": result.http,
                "attempts": result.attempts,
                "endpoint": "/users/me",
                "commerce_effects": 0,
                "checked_at": self.clock(),
            }


def callback_server(onboarding: ReadOnlyOnboarding, port: int, *, authorized=False):
    """Loopback HTTP receiver for a future owner-controlled HTTPS reverse proxy; never public by itself."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if not authorized:
        raise OnboardingError("OWNER_CONSENT_REQUIRED")
    with onboarding._locked():
        uri = onboarding._load()["redirect_uri"]
    expected_path = urlsplit(uri).path or "/"

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # HTTP access logs must not contain authorization codes/state in query strings.

        def do_GET(self):
            try:
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc or parsed.path != expected_path or parsed.fragment:
                    raise OnboardingError("CALLBACK_URI_MISMATCH")
                result = onboarding.finish(uri + "?" + parsed.query, authorized=True)
                status = 200
            except OnboardingError as exc:
                status, result = 400, {"state": str(exc), "commercial_capabilities": False}
            except Exception:
                status, result = 400, {"state": "CALLBACK_REJECTED", "commercial_capabilities": False}
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", port), Callback)
