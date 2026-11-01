"""Read-only freight quote regression tests (all API traffic intercepted)."""

from __future__ import annotations

import httpx
import pytest

from aicommerce.ai.freight_quotes import (
    SANDBOX_CALCULATE,
    FreightQuoteUnavailable,
    MelhorEnvioSandboxQuote,
)


def sandbox(response):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert str(req.url) == SANDBOX_CALCULATE
        assert req.headers["Authorization"] == "Bearer sandbox-test"
        assert req.headers["User-Agent"] == "AICommerce (suporte@example.com)"
        data = __import__("json").loads(req.content)
        assert data["from"] == {"postal_code": "86010010"}
        assert data["to"] == {"postal_code": "01001000"}
        assert data["volumes"] == [
            {
                "width": 10.0,
                "length": 20.0,
                "height": 15.0,
                "weight": 0.5,
            }
        ]
        return response(req)

    return MelhorEnvioSandboxQuote(
        token="sandbox-test",
        user_agent="AICommerce (suporte@example.com)",
        transport=httpx.MockTransport(handler),
    )


def args():
    return {
        "from_cep": "86010-010",
        "to_cep": "01001-000",
        "width_cm": 10,
        "length_cm": 20,
        "height_cm": 15,
        "weight_kg": "0.5",
    }


def test_normalize_sandbox_quotes_and_prefer_custom_values():
    client = sandbox(
        lambda _: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Normal",
                    "company": {"name": "Correios"},
                    "price": "25.00",
                    "custom_price": "21.45",
                    "delivery_time": 8,
                    "custom_delivery_time": 5,
                },
                {
                    "id": 2,
                    "name": "Fast",
                    "company": {"name": "Jadlog"},
                    "price": "32.90",
                    "delivery_time": 3,
                },
                {"id": 9, "error": "servico indisponivel"},
            ],
        )
    )
    quotes = client.calculate(**args())
    assert [x["service_id"] for x in quotes] == [1, 2]
    assert quotes[0]["price_minor"] == 2145
    assert quotes[0]["delivery_days"] == 5
    assert all(x["sandbox"] and not x["usable_for_commercial_sale"] for x in quotes)


@pytest.mark.parametrize(
    "response",
    [
        lambda _: httpx.Response(401),
        lambda _: httpx.Response(200, json={"unexpected": []}),
        lambda _: httpx.Response(200, text="not-json"),
    ],
)
def test_quote_fails_closed_on_missing_contract(response):
    with pytest.raises(FreightQuoteUnavailable):
        sandbox(response).calculate(**args())


def test_invalid_dimensions_ceps_and_credentials_block_before_network():
    client = MelhorEnvioSandboxQuote(None, None)
    with pytest.raises(FreightQuoteUnavailable):
        client.calculate(**args())
    valid = sandbox(lambda _: httpx.Response(200, json=[]))
    with pytest.raises(FreightQuoteUnavailable):
        valid.calculate(**{**args(), "from_cep": "wrong"})
    with pytest.raises(FreightQuoteUnavailable):
        valid.calculate(**{**args(), "weight_kg": 0})


def test_ignores_unusable_rates():
    client = sandbox(
        lambda _: httpx.Response(
            200,
            json=[
                {"id": 1, "company": {"name": "Correios"}, "price": None, "delivery_time": 2},
                {"id": 2, "company": {"name": "Correios"}, "price": "19", "delivery_time": None},
            ],
        )
    )
    assert client.calculate(**args()) == []


def test_logan_sla_with_optional_sandbox_quote(monkeypatch, client):
    from pydantic import SecretStr

    from aicommerce.ai.specs import SPECS
    from aicommerce.config import get_settings
    from aicommerce.db import session_scope

    monkeypatch.setattr(get_settings(), "research_me_sandbox_token", SecretStr("sandbox-test"))
    monkeypatch.setattr(get_settings(), "research_me_user_agent", "AICommerce (suporte@example.com)")

    def fake_calculate(self, **kwargs):
        assert kwargs == args()
        return [
            {
                "service_id": 1,
                "service_name": "Normal",
                "company_name": "Correios",
                "price_minor": 2145,
                "delivery_days": 5,
                "sandbox": True,
                "usable_for_commercial_sale": False,
            }
        ]

    monkeypatch.setattr(MelhorEnvioSandboxQuote, "calculate", fake_calculate)
    spec = SPECS["logistics.sla_check"]
    with session_scope() as db:
        context = spec.build(db, {"sandbox_quote": args()})
    assert "shipping.quote_sandbox" in context.tools_used
    assert context.facts["F_cotacao_sandbox"]["status"] == "QUOTED"
    result = spec.mock(context)
    assert any("F_cotacao_sandbox" in x["fact_ids"] for x in result["highlights"])
    assert result["risks"]
    assert spec.validate(result, context) == []


def test_logan_rejects_incomplete_quote_without_network(client):
    from aicommerce.ai.specs import SPECS, SpecError
    from aicommerce.db import session_scope

    with session_scope() as db:
        with pytest.raises(SpecError):
            SPECS["logistics.sla_check"].build(db, {"sandbox_quote": {"from_cep": "86010010"}})
