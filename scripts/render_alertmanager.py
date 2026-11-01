#!/usr/bin/env python3
"""Gera infra/env/alertmanager.yml a partir do template + infra/env/alerting.env (INF-005).

E-mail só é ativado se existir infra/env/secrets/smtp_password (senha de app do Gmail). Sem ela, os alertas
continuam visíveis no Prometheus/Alertmanager e o WhatsApp (via relay) segue funcionando.
"""

from __future__ import annotations

import pathlib
import string
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV, SECRETS = ROOT / "infra/env/alerting.env", ROOT / "infra/env/secrets"


def load_env(path: pathlib.Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def main() -> int:
    if not ENV.exists():
        print("crie infra/env/alerting.env a partir de infra/env/alerting.env.example", file=sys.stderr)
        return 2
    env = load_env(ENV)
    email_on = (SECRETS / "smtp_password").exists()
    q = lambda v: "'" + v.replace("'", "''") + "'"  # noqa: E731 — string YAML segura
    block = "\n".join([
        "    email_configs:",
        f"      - to: {q(env['ALERT_EMAIL_TO'])}",
        f"        from: {q(env['ALERT_EMAIL_FROM'])}",
        f"        smarthost: {q(env['ALERT_SMTP_HOST'])}",
        f"        auth_username: {q(env['ALERT_SMTP_USER'])}",
        "        auth_password_file: /etc/alertmanager/secrets/smtp_password",
        "        require_tls: true",
        "        send_resolved: true",
        "        headers:",
        "          Subject: '[AI Commerce] {{ .Status | toUpper }} — {{ .CommonLabels.alertname }}'",
    ]) if email_on else "    # e-mail desativado: falta infra/env/secrets/smtp_password"
    tmpl = string.Template((ROOT / "infra/observability/alertmanager.yml.tmpl").read_text())
    out = tmpl.substitute(DEFAULT_RECEIVER="email", URGENT_RECEIVER="email_whatsapp", EMAIL_BLOCK=block)
    target = ROOT / "infra/env/alertmanager.yml"
    target.write_text(out)
    target.chmod(0o644)  # lido pelo contêiner; não contém senha (só o caminho do arquivo)
    wa = env.get("AIC_WHATSAPP_PROVIDER", "dry")
    print(f"alertmanager.yml gerado · e-mail: {'ATIVO' if email_on else 'desativado (sem senha de app)'} · "
          f"WhatsApp: {'ATIVO (Meta)' if wa == 'meta' else 'simulado (dry)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
