"""Seção 4 (finalização): desenvolvimento, CI e E2E nunca consomem IA paga por acidente."""

from __future__ import annotations

import socket

import pytest

from aicommerce.config import PaidAIForbidden, Settings

PAID = [
    {"llm_provider": "google", "google_api_key": "chave"},
    {"llm_provider": "anthropic"},
    {"kb_embedder": "google", "google_api_key": "chave"},
]


@pytest.mark.parametrize("cfg", PAID)
def test_paid_ai_in_local_or_test_needs_explicit_owner_yes(cfg, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    for env in ("local", "test"):
        with pytest.raises(PaidAIForbidden, match="AIC_ALLOW_PAID_AI"):
            Settings(environment=env, **cfg)
        assert Settings(environment=env, allow_paid_ai=True, **cfg).uses_paid_ai


@pytest.mark.parametrize("cfg", PAID)
def test_ci_never_uses_paid_ai_even_with_authorization(cfg, monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(PaidAIForbidden, match="CI nunca"):
        Settings(environment="test", allow_paid_ai=True, **cfg)


def test_mock_is_the_default_and_always_allowed(monkeypatch):
    monkeypatch.setenv("CI", "true")
    s = Settings(environment="test")
    assert s.llm_provider == "mock" and s.kb_embedder == "hashing" and not s.uses_paid_ai


def test_production_owner_configuration_is_not_blocked(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    assert Settings(environment="staging", llm_provider="google", google_api_key="chave").uses_paid_ai


def test_test_suite_cannot_reach_external_hosts():
    for host in ("generativelanguage.googleapis.com", "api.anthropic.com", "api.mercadolibre.com"):
        with pytest.raises(OSError):
            socket.create_connection((host, 443), timeout=2)
