#!/usr/bin/env python3
"""Create and verify an isolated DRY_RUN staging environment before Docker deploy.

Never writes live.env, opens a public port, provisions a VPS or invokes a provider.
Use --init once to create a mode-0600 env; --check on every staging deploy.
"""

from __future__ import annotations

import argparse
import base64
import os
import pathlib
import secrets
import stat
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra/env/staging.env.example"
DEFAULT_FILE = ROOT / "infra/env/staging.env"
REQUIRED_SECRETS = (
    "AIC_JWT_SECRET",
    "POSTGRES_PASSWORD",
    "AIC_DB_APP_PASSWORD",
    "AIC_PII_KEY",
    "AIC_WOO_WEBHOOK_SECRET",
)
# Staging is an OFFLINE simulation. No live or paid-provider keys allowed, even
# if AIC_MODE happens to be DRY_RUN.
BLOCKED_KEYS = (
    "AIC_WOO_CONSUMER_KEY",
    "AIC_WOO_CONSUMER_SECRET",
    "AIC_ML_CLIENT_SECRET",
    "AIC_ML_REFRESH_TOKEN",
    "AIC_RESEARCH_ML_ACCESS_TOKEN",
    "AIC_RESEARCH_BRAVE_API_KEY",
    "AIC_RESEARCH_ME_SANDBOX_TOKEN",
    "AIC_GOOGLE_API_KEY",
)
FIXED = {
    "AIC_STACK": "staging",
    "AIC_ENVIRONMENT": "staging",
    "AIC_MODE": "DRY_RUN",
    "AIC_SEED_DEMO_USERS": "false",
    "POSTGRES_USER": "aicommerce",
    "POSTGRES_DB": "aicommerce_staging",
    "AIC_PG_PORT": "55441",
    "AIC_REDIS_PORT": "56391",
    "AIC_HTTP_PORT": "8081",
    "AIC_LLM_PROVIDER": "mock",
    "AIC_KB_EMBEDDER": "hashing",
    "AIC_RESEARCH_TREND_SOURCE": "mock",
    "AIC_RESEARCH_WEB_SOURCE": "none",
    "AIC_ALLOW_PAID_AI": "false",
    "AIC_WEB_ORIGIN": "http://127.0.0.1:8081",
    "AIC_PUBLIC_BASE_URL": "http://127.0.0.1:8081/api",
    "AIC_PUBLIC_API_URL": "http://127.0.0.1:8081/api",
}


def _parse(text: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"linha {number}: entrada sem =")
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not all(ch.isupper() or ch.isdigit() or ch == "_" for ch in key):
            errors.append(f"linha {number}: nome de variável inválido")
        elif key in values:
            errors.append(f"linha {number}: variável duplicada {key}")
        else:
            values[key] = value
    return values, errors


def render_staging(template: str) -> str:
    """Inject separate, strong staging-only secrets; avoid real integration credentials."""
    values, errors = _parse(template)
    if errors:
        raise ValueError("; ".join(errors))
    if not set(REQUIRED_SECRETS).issubset(values):
        raise ValueError("modelo staging sem todas as chaves obrigatórias")
    generated = {
        key: base64.b64encode(secrets.token_bytes(32)).decode()
        if key == "AIC_PII_KEY"
        else secrets.token_urlsafe(48)
        for key in REQUIRED_SECRETS
    }
    generated.update(FIXED)
    output: list[str] = []
    for line in template.splitlines():
        name, sep, _ = line.partition("=")
        output.append(f"{name}={generated.pop(name)}" if sep and name in generated else line)
    output.extend(["", "# Ambiente de homologação isolado — nunca publique portas nem ative LIVE."])
    output.extend(f"{key}={value}" for key, value in generated.items())
    result = "\n".join(output) + "\n"
    issues = validate_text(result)
    if issues:
        raise ValueError("modelo de staging inválido: " + "; ".join(issues))
    return result


def validate_text(text: str) -> list[str]:
    """Validate secrets and safe defaults without printing any secret values."""
    values, errors = _parse(text)
    for key, expected in FIXED.items():
        if values.get(key) != expected:
            errors.append(f"{key} deve ser {expected}")
    for key in REQUIRED_SECRETS:
        val = values.get(key, "")
        if len(val) < 40 or "__INJETAR" in val or "change-me" in val.lower():
            errors.append(f"{key}: segredo não gerado ou fraco")
    try:
        if len(base64.b64decode(values.get("AIC_PII_KEY", ""), validate=True)) != 32:
            errors.append("AIC_PII_KEY deve conter 32 bytes em base64")
    except (ValueError, base64.binascii.Error):
        errors.append("AIC_PII_KEY base64 inválida")
    for key in BLOCKED_KEYS:
        if values.get(key):
            errors.append(f"{key}: credencial externa proibida em staging")
    # Never silently accept unexpected variables (including provider credentials).
    expected, _ = _parse(TEMPLATE.read_text(encoding="utf-8"))
    allowed = set(expected) | set(FIXED)
    for key in values:
        if key not in allowed:
            errors.append(f"{key}: variável não autorizada em staging")
    if any("__INJETAR" in value for value in values.values()):
        errors.append("existem placeholders de segredo não substituídos")
    if values.get("AIC_STACK") in ("live", "production") or values.get("AIC_MODE") == "LIVE":
        errors.append("staging nunca pode operar em LIVE")
    return errors


def check_file(path: pathlib.Path) -> list[str]:
    if not path.is_file() or path.is_symlink():
        return ["arquivo de staging ausente ou symlink não permitido"]
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        return ["arquivo de staging permite acesso de grupo/terceiros: use chmod 600"]
    return validate_text(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--file", type=pathlib.Path, default=DEFAULT_FILE)
    args = parser.parse_args(argv)
    if args.init:
        if args.file.exists() or args.file.is_symlink():
            print("staging.env existente: não sobrescrevo segredos", file=sys.stderr)
            return 1
        args.file.parent.mkdir(parents=True, exist_ok=True)
        content = render_staging(TEMPLATE.read_text(encoding="utf-8"))
        fd = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        print("staging.env criado com segredos independentes (permissão 600); modo DRY_RUN")
        return 0
    problems = check_file(args.file)
    for problem in problems:
        print("staging inseguro: " + problem, file=sys.stderr)
    if problems:
        return 1
    print("staging verificado: DRY_RUN, localhost, segredos independentes, sem APIs comerciais")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
