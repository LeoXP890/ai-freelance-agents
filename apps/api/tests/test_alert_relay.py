"""Relay Alertmanager → WhatsApp: formatação, requisição à Meta, autenticação e modo dry."""

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from aicommerce import alert_relay as relay

PAYLOAD = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "AicWorkerDown", "severity": "critical"},
            "annotations": {"summary": "Worker sem ciclo há 3m"},
            "startsAt": "2026-10-24T12:00:00Z",
        },
        {
            "status": "resolved",
            "labels": {"alertname": "AicDatabaseUnavailable", "severity": "critical"},
            "annotations": {"summary": "Banco indisponível"},
            "startsAt": "2026-10-24T11:58:00Z",
        },
    ],
}
CFG = {
    "provider": "meta",
    "to": "5543900000000",
    "phone_number_id": "123",
    "token_file": "",
    "template": "aic_alerta",
    "lang": "pt_BR",
    "api_version": "v23.0",
}


def test_render_is_short_and_marks_resolved():
    text = relay.render(PAYLOAD)
    assert text.startswith("AI Commerce\n")
    assert "[CRÍTICO] AicWorkerDown: Worker sem ciclo há 3m (2026-10-24 12:00 UTC)" in text
    assert "[RESOLVIDO] AicDatabaseUnavailable" in text
    many = {"status": "firing", "alerts": PAYLOAD["alerts"] * 6}
    assert "+7 alerta(s)" in relay.render(many) and len(relay.render(many)) <= relay.MAX_TEXT


def test_meta_request_uses_template_without_newlines():
    req = relay.meta_request(CFG, "tok", "a\nb")
    body = json.loads(req.data)
    assert req.full_url == "https://graph.facebook.com/v23.0/123/messages"
    assert req.get_header("Authorization") == "Bearer tok"
    assert body["type"] == "template" and body["to"] == "5543900000000"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "a | b"


def test_dry_mode_never_calls_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("não deveria chamar a rede"))
    out = relay.deliver(PAYLOAD, {**CFG, "provider": "dry"})
    assert out["sent"] is False and "AicWorkerDown" in out["text"]


def test_meta_delivery_reads_token_from_file(monkeypatch, tmp_path):
    tok = tmp_path / "token"
    tok.write_text("segredo-de-teste\n")
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        return Resp(json.dumps({"messages": [{"id": "wamid.X"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = relay.deliver(PAYLOAD, {**CFG, "token_file": str(tok)})
    assert out == {"sent": True, "mode": "meta", "message_ids": ["wamid.X"]}
    assert seen["auth"] == "Bearer segredo-de-teste"
    with pytest.raises(RuntimeError, match="sem configuração"):
        relay.deliver(PAYLOAD, {**CFG, "to": ""})


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("AIC_ALERT_RELAY_TOKEN", "t" * 32)
    monkeypatch.setenv("AIC_WHATSAPP_PROVIDER", "dry")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, token, body=PAYLOAD):
    req = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_requires_token_and_accepts_alertmanager_webhook(server, monkeypatch):
    assert post(server + "/alertmanager", "errado")[0] == 401
    code, body = post(server + "/alertmanager", "t" * 32)
    assert code == 200 and body["mode"] == "dry"
    monkeypatch.setenv("AIC_WHATSAPP_PROVIDER", "meta")  # meta sem configuração → 502 (Alertmanager re-tenta)
    assert post(server + "/alertmanager", "t" * 32)[0] == 502
