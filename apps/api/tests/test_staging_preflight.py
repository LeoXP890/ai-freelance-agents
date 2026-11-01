"""Staging readiness must never introduce LIVE endpoints, public ports or weak secrets."""

from __future__ import annotations

import importlib.util
import pathlib
import stat

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/staging_preflight.py"
SPEC = importlib.util.spec_from_file_location("staging_preflight", SCRIPT)
assert SPEC and SPEC.loader
staging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(staging)


def sample() -> str:
    template = (ROOT / "infra/env/staging.env.example").read_text(encoding="utf-8")
    return staging.render_staging(template)


def test_generated_staging_is_isolated_and_uses_strong_secrets():
    rendered = sample()
    values, errors = staging._parse(rendered)
    assert not errors and not staging.validate_text(rendered)
    assert values["AIC_STACK"] == "staging"
    assert values["AIC_MODE"] == "DRY_RUN"
    assert values["AIC_ENVIRONMENT"] == "staging"
    assert values["AIC_PUBLIC_API_URL"] == "http://127.0.0.1:8081/api"
    assert values["AIC_RESEARCH_WEB_SOURCE"] == "none"
    assert values["AIC_LLM_PROVIDER"] == "mock"
    assert all(key in values and len(values[key]) >= 40 for key in staging.REQUIRED_SECRETS)
    assert all(key not in values for key in staging.BLOCKED_KEYS)
    assert staging._parse(sample())[0]["AIC_JWT_SECRET"] != values["AIC_JWT_SECRET"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AIC_MODE", "LIVE"),
        ("AIC_STACK", "live"),
        ("AIC_ENVIRONMENT", "production"),
        ("AIC_SEED_DEMO_USERS", "true"),
        ("AIC_PUBLIC_BASE_URL", "https://api.example.com/api"),
        ("AIC_LLM_PROVIDER", "google"),
        ("AIC_RESEARCH_WEB_SOURCE", "brave"),
        ("AIC_HTTP_PORT", "8080"),
        ("AIC_JWT_SECRET", "__INJETAR_VIA_SECRET_MANAGER__"),
    ],
)
def test_reject_unsafe_staging_configuration(key, value):
    values, _ = staging._parse(sample())
    values[key] = value
    assert staging.validate_text("\n".join(f"{k}={v}" for k, v in values.items()))


def test_reject_any_external_key_and_duplicate_lines():
    rendered = sample()
    for key in staging.BLOCKED_KEYS:
        assert staging.validate_text(rendered + f"{key}=not-allowed\n")
    assert any("duplicada" in err for err in staging.validate_text(rendered + "AIC_STACK=staging\n"))


def test_requires_owner_only_permissions_and_rejects_symlink(tmp_path):
    env = tmp_path / "staging.env"
    env.write_text(sample(), encoding="utf-8")
    env.chmod(0o644)
    assert any("chmod 600" in x for x in staging.check_file(env))
    env.chmod(0o600)
    assert not staging.check_file(env)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    link = tmp_path / "symlink.env"
    link.symlink_to(env)
    assert staging.check_file(link)


def test_init_never_overwrites_secrets(tmp_path):
    env = tmp_path / "staging.env"
    assert staging.main(["--init", "--file", str(env)]) == 0
    assert not staging.check_file(env)
    secret_before = staging._parse(env.read_text())[0]["AIC_JWT_SECRET"]
    assert staging.main(["--init", "--file", str(env)]) == 1
    assert staging._parse(env.read_text())[0]["AIC_JWT_SECRET"] == secret_before
    assert staging.main(["--check", "--file", str(env)]) == 0
