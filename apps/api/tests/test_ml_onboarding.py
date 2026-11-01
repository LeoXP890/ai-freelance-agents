import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlalchemy import func, select

from aicommerce.models import ChannelAccount
from aicommerce.providers.ml_onboarding import OnboardingError, ReadOnlyOnboarding, callback_server, redirect_uri
from aicommerce.providers.ml_readonly import MLReadOnly

URI = "https://owned.example.com/oauth/ml/readonly"
SECRET = "private-client-secret-fixture"


class Exchange:
    def __init__(self):
        self.calls = []
        self.reply = None

    def __call__(self, method, url, headers, body, timeout, *, form):
        assert method == "POST" and url == "https://api.mercadolibre.com/oauth/token" and form
        assert timeout == 15 and body["client_secret"] == SECRET
        self.calls.append(body.copy())
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply or (
            200,
            {
                "access_token": "private-access-fixture",
                "refresh_token": "private-refresh-" + str(len(self.calls)),
                "expires_in": 3600,
                "token_type": "bearer",
                "user_id": 987654,
                "scope": "read offline_access",
            },
        )


@pytest.fixture
def flow(tmp_path):
    now, exchange = [1000.0], Exchange()
    service = ReadOnlyOnboarding(tmp_path / "private", exchange=exchange, clock=lambda: now[0])
    service.configure("123456", SECRET, URI)
    return service, now, exchange


def callback(service, **query):
    url = service.start()
    state = parse_qs(urlsplit(url).query)["state"][0]
    return URI + "?" + urlencode({"state": state, "code": "private-code", **query}), url


def connected(flow):
    service, _, _ = flow
    url, _ = callback(service)
    assert service.finish(url, authorized=True)["state"] == "OAUTH_CONECTADO"
    return service


def test_pkce_encrypted_storage_and_no_commerce_account(flow, db):
    before = db.scalar(select(func.count()).select_from(ChannelAccount))
    service, _, exchange = flow
    url, authorize = callback(service)
    receipt = service.finish(url, authorized=True)
    verifier = exchange.calls[0]["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert parse_qs(urlsplit(authorize).query)["code_challenge"] == [challenge]
    assert exchange.calls[0]["redirect_uri"] == URI
    assert receipt["account_id"] == "***654" and receipt["commercial_capabilities"] is False
    assert db.scalar(select(func.count()).select_from(ChannelAccount)) == before
    raw = (service.directory / "vault.enc").read_bytes()
    assert SECRET.encode() not in raw and b"private-access-fixture" not in raw and verifier.encode() not in raw
    assert all(p.stat().st_mode & 0o077 == 0 for p in service.directory.iterdir())
    assert service.directory.stat().st_mode & 0o077 == 0
    with pytest.raises(OnboardingError, match="ALREADY_CONFIGURED"):
        service.configure("999999", "different-secret", URI)
    assert service.status()["state"] == "OAUTH_CONECTADO"


def test_consent_invalid_state_and_callback_do_not_exchange(flow):
    service, _, exchange = flow
    url, _ = callback(service)
    with pytest.raises(OnboardingError, match="CONSENT"):
        service.finish(url)
    with pytest.raises(OnboardingError, match="STATE_MISMATCH"):
        service.finish(URI + "?state=wrong&code=secret", authorized=True)
    with pytest.raises(OnboardingError, match="URI_MISMATCH"):
        service.finish(url.replace("owned.example.com", "attacker.example.com"), authorized=True)
    with pytest.raises(OnboardingError, match="PARAMETERS"):
        service.finish(url + "&code=second", authorized=True)
    assert exchange.calls == []
    service.finish(url, authorized=True)
    with pytest.raises(OnboardingError, match="ALREADY_USED"):
        service.finish(url, authorized=True)
    assert len(exchange.calls) == 1


@pytest.mark.parametrize("denied", [False, True])
def test_expired_or_denied_state_is_consumed(flow, denied):
    service, now, exchange = flow
    url, _ = callback(service, **({"error": "access_denied"} if denied else {}))
    if not denied:
        now[0] += 600
    with pytest.raises(OnboardingError, match="OWNER_DENIED" if denied else "STATE_EXPIRED"):
        service.finish(url, authorized=True)
    with pytest.raises(OnboardingError, match="ALREADY_USED"):
        service.finish(url, authorized=True)
    assert exchange.calls == []


@pytest.mark.parametrize("failure", [TimeoutError("private-code"), (429, {}), (500, {}), (400, {"error": SECRET})])
def test_uncertain_exchange_never_replays_code(flow, failure):
    service, _, exchange = flow
    url, _ = callback(service)
    exchange.reply = failure
    with pytest.raises(OnboardingError) as err:
        service.finish(url, authorized=True)
    assert "private" not in str(err.value)
    with pytest.raises(OnboardingError, match="ALREADY_USED"):
        service.finish(url, authorized=True)
    assert len(exchange.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"scope": "read write offline_access"},
        {"scope": ""},
        {"expires_in": True},
        {"expires_in": 0},
        {"token_type": "other"},
        {"user_id": None},
        {"access_token": ""},
    ],
)
def test_invalid_or_overprivileged_token_is_not_saved(flow, change):
    service, _, exchange = flow
    reply = exchange("POST", "https://api.mercadolibre.com/oauth/token", {}, {"client_secret": SECRET}, 15, form=True)[
        1
    ]
    exchange.reply = (200, {**reply, **change})
    url, _ = callback(service)
    with pytest.raises(OnboardingError, match="INVALID_TOKEN_OR_SCOPE"):
        service.finish(url, authorized=True)
    with service._locked():
        assert "access_token" not in service._load() and "refresh_token" not in service._load()


def test_refresh_serialization_rotation_and_revocation(flow):
    service = connected(flow)
    _, now, exchange = flow
    now[0] += 3590
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.refresh(authorized=True), range(6)))
    assert all(r["state"] == "OAUTH_CONECTADO" for r in results)
    assert len(exchange.calls) == 2 and exchange.calls[-1]["refresh_token"] == "private-refresh-1"
    now[0] += 3590
    exchange.reply = (400, {"error": "invalid_grant"})
    with pytest.raises(OnboardingError, match="REAUTH_REQUIRED"):
        service.refresh(authorized=True)
    with pytest.raises(OnboardingError, match="REAUTH_REQUIRED"):
        service.refresh(authorized=True)
    assert len(exchange.calls) == 3
    assert exchange.calls[-1]["refresh_token"] == "private-refresh-2"
    assert service.status()["state"] == "REAUTH_REQUIRED"


def test_refresh_crash_does_not_reuse_one_time_grant(flow):
    service = connected(flow)
    _, now, exchange = flow
    now[0] += 3590

    def crash(*args, **kwargs):
        raise KeyboardInterrupt()

    service.exchange = crash
    with pytest.raises(KeyboardInterrupt):
        service.refresh(authorized=True)
    assert service.status()["state"] == "REAUTH_REQUIRED_UNCERTAIN_EXCHANGE"
    service.exchange = exchange
    with pytest.raises(OnboardingError, match="REAUTH_REQUIRED"):
        service.refresh(authorized=True)
    assert len(exchange.calls) == 1


def test_readonly_get_matches_owner_and_redacts_personal_response(flow):
    service = connected(flow)
    calls = []

    def transport(path, token):
        calls.append(path)
        return 200, {"id": 987654, "email": "private@example.invalid", "nickname": "private-name"}, {}

    service.reader = lambda token: MLReadOnly(token, send=transport, sleep=lambda _: None)
    receipt = service.check(authorized=True)
    assert receipt["state"] == "READ_ONLY_VERIFICADO" and calls == ["/users/me"]
    assert "private" not in json.dumps(receipt) and receipt["commerce_effects"] == 0
    service.reader = lambda token: MLReadOnly(token, send=lambda *_: (200, {"id": 1}, {}), sleep=lambda _: None)
    assert service.check(authorized=True)["state"] == "ACCOUNT_MISMATCH"
    assert service.status()["state"] == "ACCOUNT_MISMATCH"
    with pytest.raises(OnboardingError, match="VALID_READONLY_TOKEN"):
        service.check(authorized=True)


def test_callback_server_binds_loopback_and_does_not_echo_query(flow, capsys):
    service, _, exchange = flow
    url, _ = callback(service)
    with pytest.raises(OnboardingError, match="CONSENT"):
        callback_server(service, 0)
    server = callback_server(service, 0, authorized=True)
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}" + urlsplit(url).path + "?" + urlsplit(url).query
    try:
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            receipt = json.loads(response.read())
            assert receipt["state"] == "OAUTH_CONECTADO"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "no-referrer"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(endpoint, timeout=2)
        assert error.value.code == 400
        assert len(exchange.calls) == 1
        assert "private-code" not in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize(
    "uri",
    [
        "http://example.com/cb",
        "https://127.0.0.1/cb",
        "https://localhost/cb",
        "https://example.com/cb?x=y",
        "https://user:secret@example.com/cb",
        "https://example.com/cb#state",
        "https://example.com/a/../cb",
    ],
)
def test_redirect_must_be_fixed_https_without_credentials(uri):
    with pytest.raises(OnboardingError, match="HTTPS"):
        redirect_uri(uri)


def test_vault_rejects_symlink_and_world_readable_files(flow, tmp_path):
    service, _, _ = flow
    link = tmp_path / "link"
    link.symlink_to(service.directory)
    with pytest.raises(OnboardingError, match="DIRECTORY"):
        ReadOnlyOnboarding(link).status()
    (service.directory / "vault.key").chmod(0o644)
    with pytest.raises(OnboardingError, match="FILE"):
        service.status()


def test_platform_user_write_scope_needs_explicit_choice_and_is_reported(tmp_path):
    """DevCenter fixa "Usuários" em leitura e escrita: `write` só passa com a escolha registrada no init."""
    now, exchange = [1000.0], Exchange()
    default = ReadOnlyOnboarding(tmp_path / "default", exchange=exchange, clock=lambda: now[0])
    default.configure("123456", SECRET, URI)
    reply = exchange("POST", "https://api.mercadolibre.com/oauth/token", {}, {"client_secret": SECRET}, 15, form=True)[
        1
    ]
    exchange.reply = (200, {**reply, "scope": "offline_access read write"})
    with pytest.raises(OnboardingError, match="INVALID_TOKEN_OR_SCOPE"):
        default.finish(callback(default)[0], authorized=True)

    opted = ReadOnlyOnboarding(tmp_path / "opted", exchange=exchange, clock=lambda: now[0])
    opted.configure("123456", SECRET, URI, accept_platform_user_write=True)
    receipt = opted.finish(callback(opted)[0], authorized=True)
    assert receipt["state"] == "OAUTH_CONECTADO"
    assert receipt["token_scopes"] == ["offline_access", "read", "write"]
    assert receipt["commercial_capabilities"] is False and "private" not in str(receipt)

    exchange.reply = (200, {**reply, "scope": "offline_access read write admin"})
    other = ReadOnlyOnboarding(tmp_path / "other", exchange=exchange, clock=lambda: now[0])
    other.configure("123456", SECRET, URI, accept_platform_user_write=True)
    with pytest.raises(OnboardingError, match="INVALID_TOKEN_OR_SCOPE"):  # nada além de write é tolerado
        other.finish(callback(other)[0], authorized=True)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((400, {"error": "invalid_grant", "message": "private detail code=private-code"}), "invalid_grant"),
        ((401, {"error": "invalid_client", "message": "private"}), "invalid_client"),
        ((400, {"error": "Private Code private-code"}), "unrecognized"),
        ((403, "private body"), "unrecognized"),
    ],
)
def test_exchange_error_keeps_only_standard_oauth_code(flow, reply, expected):
    service, _, exchange = flow
    exchange.reply = reply
    with pytest.raises(OnboardingError, match="REAUTH_REQUIRED"):
        service.finish(callback(service)[0], authorized=True)
    status = service.status()
    assert {k: status["last_exchange_error"][k] for k in ("http", "error")} == {"http": reply[0], "error": expected}
    assert "private" not in json.dumps(status)
    service.start()  # nova autorização limpa o diagnóstico anterior
    assert service.status()["last_exchange_error"] is None


@pytest.mark.parametrize(
    ("message", "hint"),
    [
        ("The redirect_uri does not match private-code", "REDIRECT_URI_MISMATCH"),
        ("invalid code_verifier for private-code", "PKCE_MISMATCH"),
        ("Error validating grant. Your authorization code may be expired", "CODE_EXPIRED"),
        ("the code was already used", "CODE_ALREADY_USED"),
        ("something private", "OTHER"),
        ("", "NONE"),
    ],
)
def test_exchange_error_hint_is_a_fixed_category(flow, message, hint):
    service, _, exchange = flow
    exchange.reply = (400, {"error": "invalid_grant", "message": message})
    with pytest.raises(OnboardingError):
        service.finish(callback(service)[0], authorized=True)
    status = service.status()
    assert status["last_exchange_error"]["hint"] == hint and "private" not in json.dumps(status)


def test_token_rejection_reports_rule_and_public_scope_names_only(flow):
    service, _, exchange = flow
    reply = exchange("POST", "https://api.mercadolibre.com/oauth/token", {}, {"client_secret": SECRET}, 15, form=True)[
        1
    ]
    exchange.reply = (200, {**reply, "scope": "offline_access read urn:ml:mktp:ads:/read-only bad<scope>"})
    with pytest.raises(OnboardingError, match="INVALID_TOKEN_OR_SCOPE"):
        service.finish(callback(service)[0], authorized=True)
    status = service.status()
    assert status["last_token_rejection"] == {
        "reason": "SCOPE_UNEXPECTED",
        "scopes": ["<unrecognized>", "offline_access", "read", "urn:ml:mktp:ads:/read-only"],
    }
    assert "private" not in json.dumps(status) and status["account_id"] is None


REAL_SCOPES_20261031 = (
    "offline_access read write urn:global:admin:info:/read-only urn:global:admin:info:/read-write "
    "urn:global:admin:oauth:/read-only urn:global:admin:oauth:/read-write urn:global:admin:users:/read-only "
    "urn:global:admin:users:/read-write urn:ml:all:publish-sync:/read-only urn:ml:mktp:metrics:/read-only "
    "urn:ml:mktp:orders-shipments:/read-only urn:ml:mktp:publish-sync:/read-only"
)


@pytest.mark.parametrize(
    ("scope", "opt_in", "accepted"),
    [
        (REAL_SCOPES_20261031, True, True),  # resposta real de 31/10 com a aplicação somente leitura
        (REAL_SCOPES_20261031, False, False),  # sem opt-in, o read-write da plataforma é recusado
        ("offline_access read urn:ml:mktp:metrics:/read-only", False, True),
        (REAL_SCOPES_20261031 + " urn:ml:mktp:publish-sync:/read-write", True, False),  # escrita de anúncios
        (REAL_SCOPES_20261031 + " urn:ml:mktp:orders-shipments:/read-write", True, False),
        (REAL_SCOPES_20261031 + " urn:global:admin:billing:/read-write", True, False),  # global fora do padrão
        (REAL_SCOPES_20261031 + " urn:ml:mktp:ads", True, False),  # sem modo explícito
    ],
)
def test_granular_scopes_marketplace_read_only_platform_default_only_with_opt_in(tmp_path, scope, opt_in, accepted):
    now, exchange = [1000.0], Exchange()
    service = ReadOnlyOnboarding(tmp_path / "v", exchange=exchange, clock=lambda: now[0])
    service.configure("123456", SECRET, URI, accept_platform_user_write=opt_in)
    reply = exchange("POST", "https://api.mercadolibre.com/oauth/token", {}, {"client_secret": SECRET}, 15, form=True)[
        1
    ]
    exchange.reply = (200, {**reply, "scope": scope})
    if accepted:
        assert service.finish(callback(service)[0], authorized=True)["state"] == "OAUTH_CONECTADO"
    else:
        with pytest.raises(OnboardingError, match="INVALID_TOKEN_OR_SCOPE"):
            service.finish(callback(service)[0], authorized=True)
        assert service.status()["last_token_rejection"]["reason"] == "SCOPE_UNEXPECTED"


def test_research_reader_refreshes_near_expiry_and_keeps_allowlist(flow):
    service, now, exchange = flow
    service.finish(callback(service)[0], authorized=True)
    with pytest.raises(OnboardingError, match="OWNER_CONSENT"):
        service.research_reader()
    reader = service.research_reader(authorized=True)
    with pytest.raises(ValueError):
        reader.get("/sites/MLB/search?q=x")  # busca de anúncios fora da lista
    with pytest.raises(ValueError):
        reader.get("/products/MLB1/items?limit=5&offset=10")  # parâmetro não permitido
    now[0] += 3600  # expirado
    with pytest.raises(OnboardingError, match="VALID_READONLY_TOKEN_REQUIRED"):
        service.research_reader(authorized=True)
    calls = len(exchange.calls)
    service.research_reader(authorized=True, refresh_if_needed=True)
    assert len(exchange.calls) == calls + 1 and exchange.calls[-1]["grant_type"] == "refresh_token"
