"""Login do proprietário no Dropi para o servidor: abre um Chrome COMUM (perfil temporário próprio, sem modo
automação — o Google bloqueia login em navegador automatizado), espera o login e salva SÓ a sessão (cookies do Dropi)
em arquivo privado 0600. A senha é digitada pelo proprietário e nunca passa por este script.
Uso: uv run --with playwright python scripts/dropi_login.py <saida.json>
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROBE = """async () => { const fd = new FormData(); fd.append('page', '0'); fd.append('search', 'forma');
  try { const r = await fetch('/MarketPlace/carregaMarketPlace/dropnacional', {method: 'POST', body: fd, credentials: 'include'});
        const j = await r.json(); return !!(j.call && j.call.success); } catch (e) { return false; } }"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


with tempfile.TemporaryDirectory() as profile:
    port = free_port()
    chrome = subprocess.Popen(
        [CHROME, f"--user-data-dir={profile}", f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
         "--no-first-run", "--no-default-browser-check", "--new-window", "https://app.dropi.com.br/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("Janela aberta: faça login no Dropi (pode usar o Google). Aguardando até 10 minutos…", flush=True)
    try:
        time.sleep(4)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            deadline = time.time() + 600
            saved = False
            while time.time() < deadline and not saved:
                time.sleep(4)
                for page in ctx.pages:
                    if page.url.startswith("https://app.dropi.com.br/") and "login" not in page.url:
                        try:
                            ok = page.evaluate(PROBE)
                        except Exception:
                            ok = False
                        if ok:
                            cookies = [c for c in ctx.cookies() if "dropi" in c["domain"]]
                            fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                            with os.fdopen(fd, "w") as f:
                                json.dump({"cookies": cookies, "saved_at": time.time()}, f)
                            print("SESSAO_SALVA", flush=True)
                            saved = True
                            break
            if not saved:
                print("TEMPO_ESGOTADO", flush=True)
    finally:
        chrome.terminate()
