#!/usr/bin/env python3
"""Gera infra/env/live.env a partir do live.env.example com segredos fortes e aleatórios (QA-006).

Preenche tudo que está marcado __INJETAR_VIA_SECRET_MANAGER__ (JWT, senhas do banco, chave de dados pessoais,
segredo do webhook, token de métricas), define o domínio da API e cria a senha dos backups em
infra/env/secrets/backup_passphrase. Nunca sobrescreve um live.env existente (segredos trocados = dados cifrados
ilegíveis e sessões derrubadas). Arquivos criados com permissão 600 e fora do git.

Uso (na VPS, na pasta do projeto):  python3 scripts/gen_live_env.py --domain api.sualoja.com.br
Depois: edite o live.env só para colar as credenciais da loja/ML/Gemini quando as tiver (passos 5, 7 e 8 do guia).
"""

from __future__ import annotations

import argparse
import base64
import os
import pathlib
import re
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "infra/env/live.env.example"
TARGET = ROOT / "infra/env/live.env"
PASSPHRASE = ROOT / "infra/env/secrets/backup_passphrase"
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def secret_for(key: str) -> str:
    if key == "AIC_PII_KEY":  # AES-256: 32 bytes em base64
        return base64.b64encode(secrets.token_bytes(32)).decode()
    return secrets.token_urlsafe(48)  # [A-Za-z0-9_-]: seguro dentro de URL de conexão do banco


def render(example: str, domain: str, passphrase: pathlib.Path = PASSPHRASE) -> str:
    fixed = {
        "AIC_DOMAIN": domain,
        "AIC_WEB_ORIGIN": f"https://{domain}",
        "AIC_PUBLIC_BASE_URL": f"https://{domain}/api",
        "AIC_BACKUP_PASSPHRASE_FILE": str(passphrase),
    }
    out: list[str] = []
    for line in example.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in fixed:  # cada chave aparece uma vez só (valor duplicado em env file confunde)
            line = f"{key}={fixed.pop(key)}"
        elif sep and key.startswith(("AIC_", "POSTGRES_")) and "__INJETAR" in value:
            line = f"{key}={secret_for(key)}"
        out.append(line)
    out += ["", "# ── Gerado por scripts/gen_live_env.py ──", *(f"{k}={v}" for k, v in fixed.items()),
            "# Cópia dos backups fora da VPS (rclone; ex.: b2:aicommerce-backups ou gdrive:AICommerce/backups):",
            "# AIC_BACKUP_REMOTE="]
    return "\n".join(out) + "\n"


def write_private(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # falha se já existir
    with os.fdopen(fd, "w") as f:
        f.write(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--domain", required=True, help="domínio da API, ex.: api.sualoja.com.br")
    ap.add_argument("--target", type=pathlib.Path, default=TARGET)
    ap.add_argument("--passphrase", type=pathlib.Path, default=PASSPHRASE)
    a = ap.parse_args(argv)
    domain = a.domain.strip().lower().removeprefix("https://").rstrip("/")
    if not DOMAIN_RE.match(domain):
        print(f"domínio inválido: {a.domain!r} (use só o nome, ex.: api.sualoja.com.br)", file=sys.stderr)
        return 2
    if a.target.exists():
        print(f"{a.target} já existe — não sobrescrevo (trocar segredos torna dados cifrados ilegíveis).",
              file=sys.stderr)
        return 1
    write_private(a.target, render(EXAMPLE.read_text(), domain, a.passphrase))
    if not a.passphrase.exists():
        write_private(a.passphrase, secrets.token_urlsafe(48) + "\n")
    print(f"criado {a.target} (600) e senha de backup em {a.passphrase}.")
    print("GUARDE uma cópia dos dois arquivos fora da VPS (gerenciador de senhas): sem eles, backups e dados")
    print("pessoais cifrados não podem ser recuperados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
