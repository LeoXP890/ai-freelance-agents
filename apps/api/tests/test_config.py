import pytest

from aicommerce.config import LiveCredentialsInDryRun, RuntimeMode, Settings


def test_dry_run_refuses_to_start_with_live_credentials(monkeypatch):
    monkeypatch.setenv("ML_CLIENT_SECRET", "x")
    with pytest.raises(LiveCredentialsInDryRun, match="ML_CLIENT_SECRET"):
        Settings(mode=RuntimeMode.DRY_RUN)


@pytest.mark.parametrize("var", ["LIVE_ANYTHING", "WOO_CONSUMER_SECRET", "FISCAL_PROVIDER_TOKEN", "PAYMENT_KEY"])
def test_every_live_prefix_is_guarded(monkeypatch, var):
    monkeypatch.setenv(var, "x")
    with pytest.raises(LiveCredentialsInDryRun):
        Settings()


def test_live_rejects_dev_secret_and_demo_seed():
    for weak in (
        "dev-only-change-me-dev-only-change-me",
        "test-secret-test-secret-test-secret-0123",
        "__INJETAR_VIA_SECRET_MANAGER__xxxxxxxxxxxx",
    ):
        with pytest.raises(ValueError, match="jwt_secret"):
            Settings(mode=RuntimeMode.LIVE, jwt_secret=weak)
    with pytest.raises(ValueError, match="seed_demo_users"):
        Settings(mode=RuntimeMode.LIVE, jwt_secret="z" * 40, seed_demo_users=True)


def test_default_mode_is_dry_run():
    assert Settings().mode is RuntimeMode.DRY_RUN


def test_cors_allows_desktop_app_origins(client):
    for origin in ("tauri://localhost", "http://tauri.localhost"):
        r = client.options(
            "/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    evil = client.options(
        "/v1/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in evil.headers
