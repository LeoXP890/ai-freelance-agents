"""Tests for the optional read-only research connector. No external network calls."""

import httpx
import pytest

from aicommerce.ai.real_trends import MercadoLivreTrendSource, ResearchUnavailable


def source(response):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.mercadolibre.com"
        assert request.url.path == "/trends/MLB"
        assert request.headers["Authorization"] == "Bearer test-token"
        return response(request)

    return MercadoLivreTrendSource(access_token="test-token", transport=httpx.MockTransport(handler))


def test_research_is_readonly_and_preserves_marketplace_source():
    s = source(
        lambda _: httpx.Response(
            200,
            json=[
                {"keyword": "organizador de cabos"},
                {"keyword": "pote hermetico"},
                {"keyword": "organizador de cabos"},
                {"keyword": "suporte notebook"},
            ],
        )
    )
    result = s.search("organizador", 5)
    assert len(result) == 1
    assert result[0]["title"] == "organizador de cabos"
    assert result[0]["simulated"] is False
    assert result[0]["verified_sales"] is False
    assert result[0]["source_api"] == "https://api.mercadolibre.com/trends/MLB"
    assert result[0]["url"] == "https://api.mercadolibre.com/trends/MLB"


@pytest.mark.parametrize(
    "response",
    [
        lambda _: httpx.Response(401, json={"error": "unauthorized"}),
        lambda _: httpx.Response(200, json={"unrecognized": "shape"}),
        lambda _: httpx.Response(200, text="not JSON"),
    ],
)
def test_unverified_response_fails_closed(response):
    with pytest.raises(ResearchUnavailable):
        source(response).search("organizador")


def test_empty_data_is_empty_not_made_up():
    assert source(lambda _: httpx.Response(200, json=[])).search("organizador") == []


def test_input_required():
    with pytest.raises(ResearchUnavailable):
        MercadoLivreTrendSource().search("  ")
