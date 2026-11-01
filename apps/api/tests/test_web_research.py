"""Read-only Brave evidence connector tests; never call the internet."""

import httpx
import pytest

from aicommerce.ai.source_catalog import SOURCE_CATALOG, sources_for
from aicommerce.ai.web_research import BraveWebResearch, WebResearchUnavailable


def source(reply):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.search.brave.com"
        assert request.url.path == "/res/v1/web/search"
        assert request.headers["X-Subscription-Token"] == "test-api-key"
        assert request.url.params["country"] == "BR"
        assert request.url.params["search_lang"] == "pt"
        return reply(request)

    return BraveWebResearch("test-api-key", transport=httpx.MockTransport(handler))


def test_returns_unverified_source_citations():
    response = {
        "web": {
            "results": [
                {
                    "url": "https://example.org/distribuidor",
                    "title": "Distribuidor",
                    "description": "Cotação sob consulta",
                },
                {"url": "https://example.org/distribuidor", "title": "Duplicado"},
                {"url": "http://example.org/unsafe", "title": "Inseguro"},
            ]
        }
    }
    rows = source(lambda _: httpx.Response(200, json=response)).search("atacado nacional", 5)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.org/distribuidor"
    assert rows[0]["verified"] is False
    assert rows[0]["approval_status"] == "UNREVIEWED"


@pytest.mark.parametrize(
    "reply",
    [
        lambda _: httpx.Response(401, json={"error": "token"}),
        lambda _: httpx.Response(200, json={"bad": "contract"}),
        lambda _: httpx.Response(200, text="not json"),
    ],
)
def test_fails_closed_without_inventing_results(reply):
    with pytest.raises(WebResearchUnavailable):
        source(reply).search("fornecedores Brasil")


def test_missing_key_and_invalid_query():
    with pytest.raises(WebResearchUnavailable):
        BraveWebResearch().search("distribuidores")
    with pytest.raises(WebResearchUnavailable):
        source(lambda _: httpx.Response(200, json={"web": {"results": []}})).search("x")


def test_all_13_agents_have_source_plan():
    expected = {
        "atlas",
        "luna",
        "nexus",
        "dante",
        "finn",
        "nova",
        "clara",
        "vera",
        "iris",
        "eva",
        "logan",
        "orion",
        "pixel",
    }
    assert set(SOURCE_CATALOG) == expected
    assert all(sources_for(agent) for agent in expected)
    assert all(
        row["status"] in {"internal", "ready_in_code", "requires_connector", "requires_credentials"}
        for sources in SOURCE_CATALOG.values()
        for row in sources
    )
    with pytest.raises(ValueError):
        sources_for("desconhecido")


def _gemini_reply(text, domains):
    return 200, {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                                "title": d,
                            }
                        }
                        for d in domains
                    ]
                },
            }
        ]
    }


def test_gemini_grounded_research_keeps_only_domains_returned_by_google():
    from aicommerce.ai.web_research import GeminiGroundedResearch

    sent = {}

    def post(url, headers, body, timeout):
        sent.update(url=url, headers=headers, body=body)
        return _gemini_reply(
            "1. Universo Drop | https://universodrop.com.br/produto/escorredor-inox | dropshipping nacional\n"
            "2. Inventado | https://fornecedor-que-nao-existe.com/x | inventado pelo modelo\n"
            "- Loja | http://inseguro.com.br/a | sem https\n",
            ["universodrop.com.br", "www.trimmovel.com.br"],
        )

    out = GeminiGroundedResearch("k", post=post).search("escorredor inox fornecedor dropshipping", limit=5)
    assert [o["url"] for o in out] == ["https://universodrop.com.br/produto/escorredor-inox"]
    assert out[0]["verified"] is False and out[0]["simulated"] is False
    assert sent["body"]["tools"] == [{"google_search": {}}] and sent["headers"]["x-goog-api-key"] == "k"
    assert "k" not in sent["url"]  # chave nunca na URL


def test_gemini_grounded_research_without_google_sources_is_discarded():
    import pytest as _pytest

    from aicommerce.ai.web_research import GeminiGroundedResearch, WebResearchUnavailable

    with _pytest.raises(WebResearchUnavailable, match="sem fontes"):
        GeminiGroundedResearch("k", post=lambda *a: _gemini_reply("X | https://a.com | y", [])).search("abc fornecedor")
    with _pytest.raises(WebResearchUnavailable, match="falta"):
        GeminiGroundedResearch(None).search("abc fornecedor")


def test_gemini_grounded_research_reads_markdown_links():
    from aicommerce.ai.web_research import GeminiGroundedResearch

    text = (
        "### 1. **Bella Janela**\n* **URL:** [bellajanelaatacado.com.br](https://bellajanelaatacado.com.br)\n"
        "* outro: https://site-inventado.com/x\n"
    )
    out = GeminiGroundedResearch("k", post=lambda *a: _gemini_reply(text, ["bellajanelaatacado.com.br"])).search(
        "cortina blackout atacado"
    )
    assert [(o["title"], o["url"]) for o in out] == [("bellajanelaatacado.com.br", "https://bellajanelaatacado.com.br")]
