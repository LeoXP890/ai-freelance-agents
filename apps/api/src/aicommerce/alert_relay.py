"""Relay de alertas Alertmanager → WhatsApp (INF-005).

O Alertmanager não fala WhatsApp: ele envia um webhook para cá (com token), e o relay entrega pela API oficial
WhatsApp Cloud (Meta). Mensagem iniciada pela empresa exige TEMPLATE aprovado no WhatsApp Manager; o texto do
alerta vai como parâmetro do template. Sem credenciais o relay roda em modo "dry": registra o que enviaria.

Nunca inclui dados pessoais: só nome do alerta, severidade, resumo e quando — o que já está nas anotações.

Execução: `python -m aicommerce.alert_relay` (porta 9095). Configuração por variáveis de ambiente:
  AIC_ALERT_RELAY_TOKEN            segredo compartilhado com o Alertmanager (obrigatório)
  AIC_WHATSAPP_PROVIDER            meta | dry (padrão dry)
  AIC_WHATSAPP_TO                  destino em E.164 sem "+", ex.: 5543999999999
  AIC_WHATSAPP_PHONE_NUMBER_ID     id do número remetente (WhatsApp Manager)
  AIC_WHATSAPP_TOKEN_FILE          arquivo com o token de acesso (system user) — nunca em variável/commit
  AIC_WHATSAPP_TEMPLATE            nome do template aprovado (padrão aic_alerta), 1 parâmetro de corpo
  AIC_WHATSAPP_TEMPLATE_LANG       idioma do template (padrão pt_BR)
  AIC_WHATSAPP_API_VERSION         versão da Graph API (padrão v23.0 — conferir a vigente no painel da Meta)
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

log = logging.getLogger("aicommerce.alert_relay")
MAX_TEXT = 900  # parâmetro de template do WhatsApp aceita até ~1024 caracteres
SEVERITY_LABEL = {"critical": "CRÍTICO", "warning": "AVISO", "info": "INFO"}


def render(payload: dict[str, Any]) -> str:
    """Texto curto, sem PII, a partir do webhook do Alertmanager (formato v4)."""
    status = payload.get("status", "firing")
    lines = []
    for a in payload.get("alerts", [])[:5]:
        lab, ann = a.get("labels", {}), a.get("annotations", {})
        sev = SEVERITY_LABEL.get(lab.get("severity", ""), lab.get("severity", "").upper() or "ALERTA")
        prefix = "RESOLVIDO" if a.get("status") == "resolved" else sev
        when = (a.get("startsAt") or "")[:16].replace("T", " ")
        lines.append(f"[{prefix}] {lab.get('alertname', 'alerta')}: {ann.get('summary', '')} ({when} UTC)")
    extra = len(payload.get("alerts", [])) - 5
    if extra > 0:
        lines.append(f"+{extra} alerta(s) — ver Grafana/Prometheus")
    head = "AI Commerce" + (" — resolvido" if status == "resolved" else "")
    text = head + "\n" + "\n".join(lines)
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def meta_request(cfg: dict[str, str], token: str, text: str) -> urllib.request.Request:
    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_number_id']}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": cfg["to"],
        "type": "template",
        "template": {
            "name": cfg["template"],
            "language": {"code": cfg["lang"]},
            # parâmetro de template não aceita quebra de linha: vira " | "
            "components": [{"type": "body", "parameters": [{"type": "text", "text": text.replace("\n", " | ")}]}],
        },
    }
    return urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def config() -> dict[str, str]:
    return {
        "provider": os.environ.get("AIC_WHATSAPP_PROVIDER", "dry"),
        "to": os.environ.get("AIC_WHATSAPP_TO", ""),
        "phone_number_id": os.environ.get("AIC_WHATSAPP_PHONE_NUMBER_ID", ""),
        "token_file": os.environ.get("AIC_WHATSAPP_TOKEN_FILE", ""),
        "template": os.environ.get("AIC_WHATSAPP_TEMPLATE", "aic_alerta"),
        "lang": os.environ.get("AIC_WHATSAPP_TEMPLATE_LANG", "pt_BR"),
        "api_version": os.environ.get("AIC_WHATSAPP_API_VERSION", "v23.0"),
    }


def deliver(payload: dict[str, Any], cfg: dict[str, str] | None = None) -> dict[str, Any]:
    cfg = cfg or config()
    text = render(payload)
    if cfg["provider"] != "meta":
        log.info("modo dry: WhatsApp NÃO enviado — texto: %s", text.replace("\n", " | "))
        return {"sent": False, "mode": "dry", "text": text}
    missing = [k for k in ("to", "phone_number_id", "token_file") if not cfg[k]]
    if missing:
        raise RuntimeError(f"WhatsApp sem configuração: {missing}")
    with open(cfg["token_file"]) as f:
        token = f.read().strip()
    try:
        with urllib.request.urlopen(meta_request(cfg, token, text), timeout=15) as r:
            resp = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # erro da Meta: registra sem o token
        detail = e.read()[:300].decode(errors="replace")
        log.error("WhatsApp recusou: %s %s", e.code, detail)
        raise RuntimeError(f"WhatsApp HTTP {e.code}") from e
    return {"sent": True, "mode": "meta", "message_ids": [m.get("id") for m in resp.get("messages", [])]}


class Handler(BaseHTTPRequestHandler):
    server_version = "aic-alert-relay"

    def _reply(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._reply(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self):
        expected = os.environ.get("AIC_ALERT_RELAY_TOKEN", "")
        got = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not expected or not hmac.compare_digest(got, expected):
            return self._reply(401, {"error": "token inválido"})
        if self.path != "/alertmanager":
            return self._reply(404, {"error": "rota desconhecida"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            out = deliver(payload)
        except Exception as e:
            log.error("falha ao entregar alerta: %s", e)
            return self._reply(502, {"error": str(e)})
        return self._reply(200, out)

    def log_message(self, fmt: str, *args: Any) -> None:  # silencia o log padrão (sem cabeçalhos)
        return


def main() -> None:
    from aicommerce.observability import configure_json_logging

    configure_json_logging()
    if not os.environ.get("AIC_ALERT_RELAY_TOKEN"):
        raise SystemExit("AIC_ALERT_RELAY_TOKEN é obrigatório")
    port = int(os.environ.get("AIC_ALERT_RELAY_PORT", "9095"))
    log.info("relay de alertas ouvindo na porta %d (provedor %s)", port, config()["provider"])
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
