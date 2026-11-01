"""Provedor Gemini (Google) sem rede: transporte falso. Garante formato do pedido (chave no cabeçalho, nunca na
URL; schema JSON), leitura da resposta, custo pela família do modelo e erros tipados (recusa, truncamento,
temporário). O padrão continua sendo o mock — só liga com AIC_LLM_PROVIDER=google + AIC_GOOGLE_API_KEY."""

from __future__ import annotations

import pytest

from aicommerce.ai import llm

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
REQ = llm.LLMRequest(
    model="gemini-2.5-flash", system="Você é a Luna.", user="Tendências?", schema=SCHEMA, max_tokens=500
)


def ok(text='{"answer": "garrafas térmicas"}', **over):
    body = {
        "candidates": [
            {"content": {"parts": [{"text": "pensando…", "thought": True}, {"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": 120,
            "cachedContentTokenCount": 20,
            "candidatesTokenCount": 30,
            "thoughtsTokenCount": 10,
        },
        "modelVersion": "gemini-2.5-flash-001",
        "responseId": "resp-1",
    }
    body.update(over)
    return body


class Fake:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_request_shape_and_result():
    fake = Fake((200, ok()))
    r = llm.GoogleLLMProvider(api_key="segredo-teste", post=fake).complete(REQ)
    url, headers, body = fake.calls[0]
    assert url.endswith("/models/gemini-2.5-flash:generateContent") and "segredo-teste" not in url
    assert headers["x-goog-api-key"] == "segredo-teste"
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "Você é a Luna."
    assert (
        r.output == {"answer": "garrafas térmicas"} and r.provider == "google" and not llm.GoogleLLMProvider.simulated
    )
    assert (r.input_tokens, r.cached_tokens, r.output_tokens) == (100, 20, 40) and r.request_ref == "resp-1"
    # custo pela família (gemini-2.5-flash-001 → gemini-2.5-flash), não pelo fallback mais caro
    assert llm.cost_micro_usd(r.model, 100, 20, 40) == round(100 * 0.30 + 20 * 0.075 + 40 * 2.50 + 0.4999)


def test_schema_unsupported_falls_back_to_prompt_and_still_parses():
    fake = Fake((400, {"error": {"message": "Invalid JSON schema field"}}), (200, ok()))
    r = llm.GoogleLLMProvider(api_key="k", post=fake).complete(REQ)
    assert r.output["answer"] and len(fake.calls) == 2
    second = fake.calls[1][2]
    assert (
        "responseJsonSchema" not in second["generationConfig"]
        and "JSON Schema" in second["contents"][0]["parts"][0]["text"]
    )


@pytest.mark.parametrize(
    ("resp", "err"),
    [
        ((429, {"error": {"message": "quota"}}), llm.LLMTransient),
        ((503, {}), llm.LLMTransient),
        (TimeoutError("lento"), llm.LLMTransient),
        ((403, {"error": {"message": "API key not valid"}}), llm.LLMError),
        ((200, {"promptFeedback": {"blockReason": "SAFETY"}}), llm.LLMRefusal),
        ((200, ok(candidates=[{"content": {"parts": []}, "finishReason": "SAFETY"}])), llm.LLMRefusal),
        (
            (200, ok(candidates=[{"content": {"parts": [{"text": '{"answer": "co'}]}, "finishReason": "MAX_TOKENS"}])),
            llm.LLMTruncated,
        ),
        ((200, ok(text="não é json")), llm.LLMError),
    ],
)
def test_typed_errors(resp, err):
    with pytest.raises(err):
        llm.GoogleLLMProvider(api_key="k", post=Fake(resp)).complete(REQ)


def test_settings_and_routing(monkeypatch):
    from aicommerce.config import Settings, get_settings

    # só confere a configuração/roteamento (rede externa bloqueada); fora do CI, como numa máquina autorizada —
    # no CI a configuração recusa IA paga por regra (test_paid_ai_guard.py)
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(ValueError, match="AIC_GOOGLE_API_KEY"):
        Settings(llm_provider="google", allow_paid_ai=True)
    # só monta a configuração para conferir o roteamento (nenhuma chamada sai: rede externa bloqueada nos testes)
    s = Settings(llm_provider="google", google_api_key="chave-secreta-xyz", allow_paid_ai=True)
    assert s.google_api_key.get_secret_value() == "chave-secreta-xyz" and "chave-secreta-xyz" not in repr(
        s
    )  # segredo não vaza em repr/log
    monkeypatch.setattr("aicommerce.ai.llm.get_settings", lambda: s)
    assert llm.route(spec_tier="small").model == "gemini-3.8-flash"  # 2.5 indisponível para chaves novas
    assert llm.route(spec_tier="small", risk="HIGH").model == "gemini-3.1-pro-preview"
    assert get_settings().llm_provider == "mock"  # o ambiente de teste/DRY_RUN continua no mock


def test_gemini_3_uses_thinking_level_and_conservative_price():
    from aicommerce.ai.llm import GoogleLLMProvider

    req = llm.LLMRequest(model="gemini-3.8-flash", system="s", user="u", schema=SCHEMA, max_tokens=500, effort="low")
    body = GoogleLLMProvider(api_key="k", post=lambda *a: (500, {}))._body(req, True)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert llm.thinking_budget("gemini-3.8-flash", "low") == 1024  # estimativa para a reserva de custo
    assert llm.cost_micro_usd("gemini-3.8-flash", 1_000_000, 0, 0) == 1_500_000  # preço de 2027 (não subestima)
