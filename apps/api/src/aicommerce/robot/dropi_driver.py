# ruff: noqa: E501 — trechos de JavaScript executados na página do Dropi
"""Operação do painel do Dropi com Chromium headless (Playwright) e a sessão do proprietário.

Faz o mesmo caminho validado à mão em 31/10/2026: importar → título → preço → categoria do Mercado Livre →
marca/modelo → Salvar com "Exportar/Atualizar este produto no marketplace Mercado Livre". Nunca aceita termos,
nunca preenche dado que não sabe: para com NEEDS_OWNER e a foto da tela. `rehearse=True` faz tudo menos salvar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aicommerce.config import get_settings
from aicommerce.robot import Job, Outcome

BASE = "https://app.dropi.com.br"
# O seletor de categorias do Dropi usa nomes antigos em alguns ramos do Mercado Livre.
ROOT_ALIASES = {"Pet Shop": "Animais"}

PICK_CATEGORY = """async ({path, aliases}) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const sel = () => [...document.querySelectorAll('select')].find(s => s.offsetParent !== null && s.name.endsWith('[category_id]'));
  const pick = async (label) => {
    const s = sel(); if (!s || !label) return null;
    const o = [...s.options].find(o => o.text.trim() === label); if (!o) return null;
    s.value = o.value; s.dispatchEvent(new Event('change', {bubbles: true})); await sleep(2500); return o.value;
  };
  if (!sel()) return {ok: false, why: 'seletor de categoria do Mercado Livre não apareceu'};
  if (!(await pick(path[0])) && !(await pick(aliases[path[0]]))) return {ok: false, why: 'categoria raiz não encontrada: ' + path[0]};
  for (const name of path.slice(1)) { if (!(await pick(name))) return {ok: false, why: 'subcategoria não encontrada: ' + name}; }
  return {ok: true, value: sel().value};
}"""
FILL = """({name, value}) => {
  const el = document.querySelector(`[name="${name}"]`); if (!el) return false;
  if (el.value) return true;
  const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); return true;
}"""
MISSING = """() => [...document.querySelectorAll('input,select,textarea')]
  .filter(e => e.offsetParent !== null && e.required && !e.value && /\\[attr_/.test(e.name))
  .map(e => e.name.replace(/^.*\\[attr_/, '').replace(/\\]$/, ''))"""
FIND_IMPORT = """async (title) => {
  const fd = new FormData(); fd.append('page', '0'); fd.append('search', title);
  const j = await (await fetch('/MarketPlace/carregaMarketPlace/dropnacional', {method: 'POST', body: fd, credentials: 'include'})).json();
  const p = ((j.call || {}).success || {}).produtos || [];
  const hit = p.find(x => (x.title || '').trim() === title.trim()) || null;
  return hit ? {url: hit.link_importar, imported: !!hit.importado} : null;
}"""


def _brl(minor: int) -> str:
    return f"{minor / 100:.2f}".replace(".", ",")


class PlaywrightDropi:
    def __init__(self, *, headless: bool = True) -> None:
        s = get_settings()
        self.session_file = s.dropi_session_file
        self.evidence_dir = Path(s.robot_evidence_dir or "/tmp/aicommerce-robot")
        self.headless = headless

    def _cookies(self) -> list[dict[str, Any]]:
        if not self.session_file or not Path(self.session_file).exists():
            raise RuntimeError("sessão do Dropi ausente no servidor")
        out = []
        for c in json.loads(Path(self.session_file).read_text()).get("cookies", []):
            ck = {k: c[k] for k in ("name", "value", "domain", "path") if k in c}
            ck.update({"secure": bool(c.get("secure")), "httpOnly": bool(c.get("httpOnly"))})
            if c.get("expires", -1) and c.get("expires", -1) > 0:
                ck["expires"] = c["expires"]
            if c.get("sameSite") in ("Strict", "Lax", "None"):
                ck["sameSite"] = c["sameSite"]
            out.append(ck)
        return out

    def publish(self, job: Job, *, rehearse: bool) -> Outcome:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright

        folder = self.evidence_dir / str(job.proposal_id)
        folder.mkdir(parents=True, exist_ok=True)
        shots: list[str] = []
        step = "abrindo o Dropi"

        def shot(page, name: str) -> None:
            path = folder / f"{len(shots) + 1:02d}-{name}.png"
            page.screenshot(path=str(path), full_page=False)
            shots.append(str(path))

        def stop(state: str, message: str, page=None, edit_url: str | None = None) -> Outcome:
            if page is not None:
                try:
                    shot(page, state.lower())
                except Exception:
                    pass
            return Outcome(state, message, step, edit_url, shots)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, args=["--disable-dev-shm-usage"])
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
            ctx.add_cookies(self._cookies())
            page = ctx.new_page()
            page.set_default_timeout(30_000)
            try:
                edit_url = job.edit_url
                if not edit_url:
                    step = "localizar o produto no Dropi"
                    page.goto(f"{BASE}/", wait_until="domcontentloaded")
                    if "login" in page.url:
                        return stop("NEEDS_OWNER", "sessão do Dropi expirou: entrar de novo na janela de login", page)
                    target = job.import_url
                    if not target and job.dropi_title:
                        hit = page.evaluate(FIND_IMPORT, job.dropi_title)
                        if hit and hit.get("imported"):
                            return stop(
                                "NEEDS_OWNER",
                                "produto já importado no Dropi: abra-o em Produtos e "
                                "informe o link de edição ao robô (evita duplicar)",
                                page,
                            )
                        target = (hit or {}).get("url")
                    if not target:
                        return stop("FAILED", "produto não encontrado no catálogo do Dropi", page)
                    step = "importar"
                    page.goto(target, wait_until="domcontentloaded")
                    page.get_by_role("button", name="Importar", exact=True).first.click()
                    try:
                        page.wait_for_url(re.compile(r"/editar/produto/"), timeout=20_000)
                    except PWTimeout:
                        if page.get_by_text("Leia e aceite os termos").count():
                            return stop(
                                "NEEDS_OWNER",
                                "o fornecedor pede aceite de termos no Dropi: é decisão sua "
                                "(abra o produto no Dropi e aceite, depois o robô continua)",
                                page,
                            )
                        return stop("FAILED", "o Dropi não abriu a edição depois de importar", page)
                    edit_url = page.url.split("?")[0]
                else:
                    page.goto(edit_url, wait_until="domcontentloaded")
                shot(page, "edicao")

                step = "título"
                title_input = page.locator(
                    "xpath=//*[normalize-space(text())='Mudar Título']/following::input[1]"
                ).first
                title_input.fill(job.title)

                step = "preço"
                page.get_by_text("Preços", exact=True).first.click()
                prices = page.locator('input[name="precosNovos[]"]')
                if prices.count() == 0:
                    return stop("FAILED", "campo de preço não encontrado na edição do Dropi", page, edit_url)
                for i in range(prices.count()):
                    prices.nth(i).fill(_brl(job.price_minor))
                    prices.nth(i).press("Tab")
                page.wait_for_timeout(1500)
                shot(page, "preco")
                # segunda conta, independente: o "Lucro Real" que o próprio Dropi calcula não pode ser negativo
                row = prices.nth(0).locator("xpath=ancestor::tr[1]").inner_text()
                neg = re.search(r"-\s?R\$\s?[\d.]+,\d{2}", row)
                if neg:
                    return stop(
                        "NEEDS_OWNER",
                        f"o Dropi calcula prejuízo neste preço ({neg.group(0)} por venda): não publiquei",
                        page,
                        edit_url,
                    )

                step = "categoria do Mercado Livre"
                page.get_by_text("Marketplaces", exact=True).first.click()
                page.wait_for_timeout(1500)
                res = page.evaluate(PICK_CATEGORY, {"path": job.category_path, "aliases": ROOT_ALIASES})
                if not res.get("ok"):
                    return stop("NEEDS_OWNER", f"não consegui escolher a categoria ({res.get('why')})", page, edit_url)
                if job.category_id and res.get("value") != job.category_id:
                    return stop(
                        "NEEDS_OWNER",
                        f"categoria escolhida {res.get('value')} difere da aprovada {job.category_id}",
                        page,
                        edit_url,
                    )

                step = "marca e modelo"
                for attr, value in (("BRAND", job.brand), ("MODEL", job.model)):
                    for el in page.locator(f'[name$="[attr_{attr}]"]').all():
                        page.evaluate(FILL, {"name": el.get_attribute("name"), "value": value})
                missing = page.evaluate(MISSING)
                if missing:
                    return stop(
                        "NEEDS_OWNER",
                        "o Mercado Livre exige atributos que o robô não sabe preencher: " + ", ".join(missing),
                        page,
                        edit_url,
                    )
                shot(page, "marketplace")
                if rehearse:
                    return stop(
                        "REHEARSED", "ensaio completo: tudo pronto para exportar (nada foi salvo)", page, edit_url
                    )

                step = "salvar e exportar"
                page.get_by_role("button", name="Salvar", exact=True).first.click()
                page.get_by_text("Exportar/Atualizar este produto no marketplace Mercado Livre").first.click()
                checked = page.evaluate("""() => [...document.querySelectorAll('input[type=checkbox]')]
                    .some(c => c.checked && /Exportar\\/Atualizar/.test((c.closest('label') || c.parentElement || {}).innerText || ''))""")
                if not checked:
                    return stop("FAILED", "não consegui marcar a exportação para o Mercado Livre", page, edit_url)
                shot(page, "confirmar")
                page.get_by_role("button", name="Salvar Produto").click()
                toasts: list[str] = []
                for _ in range(20):
                    page.wait_for_timeout(1500)
                    toasts = [t.strip() for t in page.locator(".iziToast").all_inner_texts() if t.strip()]
                    if toasts:
                        break
                shot(page, "resultado")
                text = " | ".join(toasts)[:400]
                if not toasts:
                    return stop("NEEDS_OWNER", "o Dropi não confirmou o salvamento; conferir no painel", page, edit_url)
                if re.search(r"erro|falha|inv[aá]lid|n[aã]o foi poss", text, re.I):
                    return stop("FAILED", f"o Dropi recusou: {text}", page, edit_url)
                return Outcome("DONE", f"Dropi: {text}", step, edit_url, shots)
            except PWTimeout:
                return stop("FAILED", f"tempo esgotado no passo '{step}'", page)
            finally:
                ctx.close()
                browser.close()
