"""Implantação: o docker-compose repassa à API/worker TODA configuração de produção que o Settings lê.

Defeito encontrado em 24/10: AIC_PII_KEY, AIC_WOO_WEBHOOK_SECRET, AIC_METRICS_TOKEN e as credenciais da loja/ML não
chegavam ao container — em LIVE a API recusaria iniciar (chaves de simulação) e a loja real nunca seria configurada.
"""

from __future__ import annotations

import base64
import pathlib
import re

import yaml

from aicommerce.config import Settings

ROOT = pathlib.Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "infra/compose/docker-compose.yml"
LIVE_EXAMPLE = ROOT / "infra/env/live.env.example"

# configurações que só fazem sentido no processo local/teste (não precisam passar pelo compose)
LOCAL_ONLY = {
    "AIC_ENVIRONMENT_FILE",
    "AIC_OFFICE_POLL_INTERVAL_MS",
    "AIC_ACCESS_TOKEN_TTL_SECONDS",
    "AIC_MFA_CHALLENGE_TTL_SECONDS",
    "AIC_MFA_ENROLL_TTL_SECONDS",
    "AIC_JWT_ISSUER",
    "AIC_LLM_MODEL_SMALL",
    "AIC_LLM_MODEL_ADVANCED",
}


def api_env() -> dict[str, str]:
    doc = yaml.safe_load(COMPOSE.read_text())
    return {k: str(v) for k, v in doc["x-api-env"].items()}


def test_every_production_setting_reaches_the_containers():
    env = api_env()
    fields = {"AIC_" + name.upper() for name in Settings.model_fields}
    missing = sorted(fields - set(env) - LOCAL_ONLY)
    assert missing == [], f"configuração não repassada pelo compose (x-api-env): {missing}"


def test_compose_defaults_are_simulation_values_that_live_rejects():
    env = api_env()
    defaults = {k: m.group(1) for k, v in env.items() if (m := re.fullmatch(r"\$\{\w+:-(.*)\}", v))}
    s = Settings(_env_file=None, pii_key=defaults["AIC_PII_KEY"])
    assert s.pii_key == defaults["AIC_PII_KEY"]  # o padrão do compose é exatamente a chave de simulação
    for key in ("AIC_WOO_CONSUMER_KEY", "AIC_WOO_CONSUMER_SECRET", "AIC_ML_CLIENT_SECRET"):
        assert defaults[key] == "", f"{key} precisa ser vazio por padrão (credencial só no live.env)"


def test_blank_credentials_from_compose_mean_not_configured_in_dry_run():
    s = Settings(mode="DRY_RUN", woo_consumer_key="", woo_consumer_secret=" ", ml_client_secret="", google_api_key="")
    assert s.woo_consumer_key is None and s.ml_client_secret is None and s.google_api_key is None


def test_live_example_filled_with_real_values_boots_in_live(monkeypatch):
    """O live.env.example lista o que é obrigatório; preenchido, o Settings aceita LIVE (e recusa se faltar algo)."""
    values: dict[str, str] = {}
    for line in LIVE_EXAMPLE.read_text().splitlines():
        if line.startswith("AIC_") and "=" in line:
            k, v = line.split("=", 1)
            values[k] = v
    for k, v in list(values.items()):
        if "__INJETAR" in v:
            values[k] = "valor-real-forte-" + "x" * 40
    values["AIC_PII_KEY"] = "cmVhbC1waWkta2V5LXByb2R1Y2FvLTMyYnl0ZXMhISE="
    for k in list(__import__("os").environ):
        if k.startswith("AIC_"):
            monkeypatch.delenv(k)
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.mode.value == "LIVE" and s.environment.value == "production"
    monkeypatch.setenv("AIC_PII_KEY", "c2ltLXBpaS1rZXktZHJ5cnVuLXNvbWVudGUtMzJieSE=")
    try:
        Settings(_env_file=None)
    except ValueError as e:
        assert "pii_key" in str(e)
    else:
        raise AssertionError("LIVE aceitou a chave de simulação")


def load_gen():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_live_env", ROOT / "scripts/gen_live_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gen_live_env_creates_strong_private_config_that_boots_live(tmp_path, monkeypatch):
    gen = load_gen()
    target, pp = tmp_path / "live.env", tmp_path / "secrets/backup_passphrase"
    args = ["--domain", "https://API.Loja.com.br/", "--target", str(target), "--passphrase", str(pp)]
    assert gen.main(args) == 0
    assert oct(target.stat().st_mode & 0o777) == "0o600" and oct(pp.stat().st_mode & 0o777) == "0o600"
    text = target.read_text()
    assert "__INJETAR" not in text and "AIC_DOMAIN=api.loja.com.br" in text
    values = dict(line.split("=", 1) for line in text.splitlines() if line.startswith(("AIC_", "POSTGRES_")))
    assert len({values[k] for k in ("AIC_JWT_SECRET", "POSTGRES_PASSWORD", "AIC_DB_APP_PASSWORD")}) == 3
    for k in list(__import__("os").environ):
        if k.startswith("AIC_"):
            monkeypatch.delenv(k)
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)  # o arquivo gerado passa em todas as travas de LIVE
    assert s.mode.value == "LIVE" and len(base64.b64decode(s.pii_key)) == 32
    before = target.read_text()
    assert gen.main(args) == 1 and target.read_text() == before  # nunca sobrescreve
    assert gen.main(["--domain", "não é domínio", "--target", str(tmp_path / "x.env")]) == 2


def test_base_compose_publishes_nothing_beyond_loopback_by_default():
    """Simulação local: nenhuma porta da base fica na rede (proxy, banco e Redis só em 127.0.0.1). Produção e staging
    sobrescrevem as portas do proxy nos próprios arquivos (80/443 com TLS; staging no loopback)."""
    doc = yaml.safe_load(COMPOSE.read_text())
    published = [p for svc in doc["services"].values() for p in svc.get("ports", [])]
    assert published, "esperava portas publicadas na base"
    for p in published:
        assert p.startswith("127.0.0.1:") or p.startswith("${AIC_HTTP_BIND:-127.0.0.1}:"), p
