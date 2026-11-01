"""Search evidence from Brave's documented read-only Web Search API.

Results are *unverified leads*, never validated suppliers, official legal opinions,
freight quotations, current stock or permission to sell. Disabled without an API key.
"""

from __future__ import annotations

import re
from typing import Any

import httpx


def _why(exc: Exception) -> str:
    """Motivo curto e seguro: tipo do erro e, se houver, o código HTTP (401/403/429/5xx) — nunca corpo/cabeçalhos."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class WebResearchUnavailable(RuntimeError):
    pass


class BraveWebResearch:
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
    name = "brave-search-readonly"

    def __init__(self, access_key: str | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        self._key = access_key
        self._transport = transport

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self._key:
            raise WebResearchUnavailable("Brave Search não configurado: falta a chave da API")
        q = query.strip()
        if len(q) < 3 or len(q) > 300:
            raise WebResearchUnavailable("Consulta de pesquisa inválida")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(8.0),
                follow_redirects=False,
                transport=self._transport,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._key,
                    "User-Agent": "AICommerceResearch/1.0",
                },
            ) as client:
                response = client.get(
                    self.ENDPOINT,
                    params={"q": q, "country": "BR", "search_lang": "pt", "count": max(1, min(limit, 10))},
                )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WebResearchUnavailable(f"Pesquisa indisponível ({_why(exc)})") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("web"), dict):
            raise WebResearchUnavailable("Resposta da pesquisa fora do contrato")
        items = payload["web"].get("results")
        if not isinstance(items, list):
            raise WebResearchUnavailable("Lista de resultados ausente")
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url, title, desc = item.get("url"), item.get("title"), item.get("description")
            if not isinstance(url, str) or not url.startswith("https://") or url in seen:
                continue
            if not isinstance(title, str) or not title.strip():
                continue
            seen.add(url)
            output.append(
                {
                    "title": title[:180],
                    "url": url,
                    "text": desc[:450] if isinstance(desc, str) else "",
                    "source_api": self.ENDPOINT,
                    "simulated": False,
                    "verified": False,
                    "approval_status": "UNREVIEWED",
                }
            )
            if len(output) >= max(1, min(limit, 10)):
                break
        return output


GEMINI_SEARCH_PROMPT = (
    "Pesquise na web (Brasil) e liste até {limit} páginas oficiais relevantes para: {query}\n"
    "Prefira sites de fornecedores/atacadistas/fabricantes com página do produto, CNPJ, termos ou contato.\n"
    "Use a ferramenta de busca do Google. Responda com linhas no formato: TÍTULO | URL https completa | por que é "
    "relevante (até 25 palavras).\n"
    "Não invente URLs: use apenas páginas encontradas na pesquisa."
)


GEMINI_TRENDS_PROMPT = (
    "Pesquise na web (Brasil, mês atual) e liste até {limit} PRODUTOS físicos em alta para comprar online em: {query}\n"
    "Use sinais reais: listas de mais vendidos, buscas subindo, matérias de tendência de varejo. Nada de marcas de "
    "luxo, alimentos, remédios ou eletrônicos que exigem homologação.\n"
    "Use a ferramenta de busca do Google. Responda com linhas no formato: NOME CURTO DO PRODUTO | URL https completa "
    "da fonte | sinal de tendência (até 15 palavras).\n"
    "Não invente URLs: use apenas páginas encontradas na pesquisa."
)


def _domain(url: str) -> str:
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class GeminiGroundedResearch:
    """Pesquisa web pelo próprio Gemini com "Grounding with Google Search" (mesma chave AIC_GOOGLE_API_KEY).

    Só aceita URLs cujo domínio aparece nas fontes que a pesquisa do Google devolveu (groundingChunks): o modelo não
    pode inventar endereço. Resultados continuam sendo pistas não verificadas, como no Brave.
    """

    name = "gemini-google-search"
    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str | None, *, model: str = "gemini-3.8-flash", post=None) -> None:
        from aicommerce.ai.llm import _http_post

        self._key, self._model, self._post = api_key, model, post or _http_post
        self.usage: dict[str, Any] | None = None

    def search(self, query: str, limit: int = 5, *, prompt: str = GEMINI_SEARCH_PROMPT) -> list[dict[str, Any]]:
        if not self._key:
            raise WebResearchUnavailable("Pesquisa Gemini não configurada: falta AIC_GOOGLE_API_KEY")
        q = query.strip()
        if len(q) < 3 or len(q) > 300:
            raise WebResearchUnavailable("Consulta de pesquisa inválida")
        limit = max(1, min(limit, 10))
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt.format(limit=limit, query=q)}]}],
            "tools": [{"google_search": {}}],
            # o raciocínio consome o limite de saída: 4.000 com nível baixo evita resposta cortada (MAX_TOKENS)
            "generationConfig": {"maxOutputTokens": 4000, "thinkingConfig": {"thinkingLevel": "low"}},
        }
        try:
            status, data = self._post(
                self.ENDPOINT.format(model=self._model),
                {"Content-Type": "application/json", "x-goog-api-key": self._key},
                body,
                60.0,
            )
        except (TimeoutError, OSError) as exc:
            raise WebResearchUnavailable(f"Pesquisa indisponível ({type(exc).__name__})") from exc
        if status != 200 or not isinstance(data, dict):
            raise WebResearchUnavailable(f"Pesquisa indisponível (HTTP {status})")
        u = data.get("usageMetadata") or {}
        cached = int(u.get("cachedContentTokenCount") or 0)
        self.usage = {  # tokens cobrados (o chamador lança no ledger de IA)
            "model": data.get("modelVersion") or self._model,
            "input": max(0, int(u.get("promptTokenCount") or 0) - cached),
            "cached": cached,
            "output": int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0),
            "ref": data.get("responseId"),
        }
        cand = (data.get("candidates") or [{}])[0]
        meta = cand.get("groundingMetadata") or {}
        grounded = {
            _domain(str(c["web"].get("uri", ""))) or str(c["web"].get("title", "")).lower().removeprefix("www.")
            for c in meta.get("groundingChunks") or []
            if isinstance(c, dict) and isinstance(c.get("web"), dict)
        }
        grounded |= {
            str(c["web"].get("title", "")).lower().removeprefix("www.")
            for c in meta.get("groundingChunks") or []
            if isinstance(c, dict) and isinstance(c.get("web"), dict)
        }
        if not grounded:
            raise WebResearchUnavailable("Pesquisa sem fontes do Google: resultado descartado")
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if isinstance(p, dict))
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Aceita "Título | URL | motivo", link markdown ou URL solta; o domínio precisa ter vindo do Google.
        for line in text.splitlines():
            for m in re.finditer(r"\[([^\]]{1,180})\]\((https://[^)\s]+)\)|(https://[^\s|)\]>]+)", line):
                url = (m.group(2) or m.group(3) or "").rstrip(".,;")
                dom = _domain(url)
                if not url or url in seen or not dom:
                    continue
                if not any(dom == g or dom.endswith("." + g) or g.endswith("." + dom) for g in grounded if g):
                    continue  # domínio não veio da pesquisa: possível invenção, descartado
                seen.add(url)
                plain = re.sub(r"[*_#>`]|\[[^\]]*\]\([^)]*\)", "", line).strip(" -|:")
                parts = [x.strip() for x in line.split("|")]
                title = m.group(1) or (parts[0].strip(" -*0123456789.") if len(parts) > 1 else "") or dom
                output.append(
                    {
                        "title": title[:180],
                        "url": url[:500],
                        "text": (parts[2] if len(parts) > 2 else plain)[:450],
                        "source_api": "gemini google_search grounding",
                        "simulated": False,
                        "verified": False,
                        "approval_status": "UNREVIEWED",
                    }
                )
                if len(output) >= limit:
                    return output
        return output
