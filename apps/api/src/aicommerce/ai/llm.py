"""Camada de LLM (AI-001, AI-009; SDD §7, §12; FR-017).

* `LLMProvider`: `MockLLMProvider` (determinístico, sem rede — padrão), `AnthropicLLMProvider` (Claude API) e
  `GoogleLLMProvider` (Gemini API, REST oficial com saída JSON por schema; sem SDK extra).
* Roteador testável: modelo pequeno por padrão; escalona para o avançado em tarefas complexas, risco alto ou
  depois de saída inválida.
* Saída sempre estruturada (JSON Schema via `output_config.format`); o código valida antes de usar.
* Custo registrado em `ai_usage` (append-only) em micro-dólares; orçamento mensal e limites por execução
  (`stop_reason=LIMIT_REACHED`). Cache por hash de conteúdo com TTL.
* Conteúdo externo (fontes, KB, mensagens de cliente) entra marcado como dado não confiável.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select

from aicommerce import faults
from aicommerce.canonical import canonical_json, sha256_of
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.models import AIUsage, LLMCache

# Preço por milhão de tokens (US$) == micro-dólares por token. Fonte: tabela de modelos da Claude API (2026-10).
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20},
    # Gemini (tabela pública do Google, prompts ≤ 200 mil tokens). CONFERIR a tabela oficial antes de produção;
    # modelo fora desta lista é precificado pelo mais caro conhecido (nunca subestima o orçamento).
    "gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40, "cache_read": 0.025},
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50, "cache_read": 0.075},
    "gemini-2.5-pro": {"in": 1.25, "out": 10.00, "cache_read": 0.31},
    "gemini-embedding-001": {"in": 0.15, "out": 0.0, "cache_read": 0.0},  # embeddings (só entrada)
    # Gemini 3 (conferido em 2026-10-31 em ai.google.dev/gemini-api/docs/pricing). 3.8 Flash custa 0,75/3,75 até
    # 31/12/2026 e dobra em 2027: usamos o preço de 2027 para o teto nunca subestimar.
    "gemini-3.8-flash": {"in": 1.50, "out": 7.50, "cache_read": 0.15},
    "gemini-3.1-pro-preview": {"in": 2.00, "out": 12.00, "cache_read": 0.20},
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MODELS_WITH_DEFAULT_FALLBACK = {"claude-opus-5-5"}


class LLMError(Exception):
    """Falha de chamada. `usage`: o provedor respondeu (e cobrou) antes do erro — o custo real vai para o ledger.
    `uncertain`: não dá para saber se cobrou (ex.: timeout depois do envio) — lança o pior caso reservado."""

    kind = "LLM_ERROR"
    retryable = False

    def __init__(self, message: str = "", *, usage: LLMResult | None = None, uncertain: bool = False) -> None:
        super().__init__(message)
        self.usage, self.uncertain = usage, uncertain


class LLMTransient(LLMError):
    kind, retryable = "TRANSIENT", True


class LLMRefusal(LLMError):
    kind = "REFUSAL"


class LLMTruncated(LLMError):
    kind = "TRUNCATED"


class LimitReached(LLMError):
    kind = "LIMIT_REACHED"


class LLMOutputInvalid(LLMError):
    """Saída fora do JSON Schema da tarefa (auditoria A06). Custo já registrado; nada vai para o cache."""

    kind = "OUTPUT_INVALID"

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


def schema_errors(output: Any, schema: dict[str, Any]) -> list[str]:
    import jsonschema

    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path)) or '(raiz)'}: {e.message}" for e in v.iter_errors(output)][:10]


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    user: str
    schema: dict[str, Any]
    max_tokens: int = 4000
    effort: str = "low"
    mock_output: dict[str, Any] | None = None  # usado só pelo provedor mock


@dataclass(frozen=True)
class LLMResult:
    output: dict[str, Any]
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    stop_reason: str
    provider: str
    request_ref: str | None = None
    cache_hit: bool = False


class LLMProvider(Protocol):
    name: str
    simulated: bool

    def complete(self, req: LLMRequest) -> LLMResult: ...


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# Teto estrito (auditoria A02): tokenizadores dos provedores (BPE em bytes / SentencePiece com byte-fallback)
# nunca geram mais tokens que bytes UTF-8; o limite superior real é o tamanho em bytes de tudo que vai na
# requisição (sistema, usuário, schema) + margem fixa de formatação da mensagem.
REQUEST_OVERHEAD_TOKENS = 64


def input_tokens_upper(system: str, user: str, schema: dict[str, Any]) -> int:
    blob = system + user + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return len(blob.encode("utf-8")) + REQUEST_OVERHEAD_TOKENS


def thinking_budget(model: str, effort: str) -> int:
    """Tokens de raciocínio do Gemini 2.5 (cobrados como saída). Fixados explicitamente para entrar na reserva:
    flash/flash-lite sem raciocínio no esforço baixo; pro exige no mínimo 128. Gemini 3 usa thinkingLevel (não dá
    para fixar tokens): o valor aqui é só a estimativa usada na reserva de custo."""
    if model.startswith("gemini-3"):
        return 1024 if effort == "low" else 4096
    if not model.startswith("gemini-2.5"):
        return 0
    if model.startswith("gemini-2.5-pro"):
        return 128 if effort == "low" else 1024
    return 0 if effort == "low" else 1024


class MockLLMProvider:
    """Determinístico e sem rede. A saída vem do `mock_output` da especificação da tarefa (fixture)."""

    name = "mock"
    simulated = True

    def __init__(self) -> None:
        self._seen_systems: set[str] = set()

    def complete(self, req: LLMRequest) -> LLMResult:
        if req.mock_output is None:
            raise LLMError("mock sem saída de fixture para esta tarefa")
        text = canonical_json(req.mock_output)
        sys_key = sha256_of(req.system)
        cached = _estimate_tokens(req.system) if sys_key in self._seen_systems else 0  # simula prompt caching
        self._seen_systems.add(sys_key)
        return LLMResult(
            output=json.loads(text),
            model=req.model,
            input_tokens=_estimate_tokens(req.system) + _estimate_tokens(req.user) - cached,
            cached_tokens=cached,
            output_tokens=_estimate_tokens(text),
            stop_reason="end_turn",
            provider=self.name,
        )


class AnthropicLLMProvider:
    """Claude API. Credenciais resolvidas pelo SDK (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN ou perfil)."""

    name = "anthropic"
    simulated = False

    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(max_retries=2, timeout=180.0)

    def complete(self, req: LLMRequest) -> LLMResult:
        a = self._anthropic
        kwargs: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            # Prefixo estável e cacheável: o system prompt não contém datas, IDs ou dados variáveis.
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": req.user}],
            "output_config": {"effort": req.effort, "format": {"type": "json_schema", "schema": req.schema}},
        }
        try:
            if req.model in MODELS_WITH_DEFAULT_FALLBACK:
                resp = self._client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = self._client.messages.create(**kwargs)
        except (a.APIConnectionError, a.APITimeoutError) as e:  # pode ter sido processada: custo incerto
            raise LLMTransient(f"{type(e).__name__}: {e}", uncertain=True) from e
        except (a.RateLimitError, a.InternalServerError) as e:
            raise LLMTransient(f"{type(e).__name__}: {e}") from e
        except a.APIStatusError as e:
            raise LLMError(f"{type(e).__name__} ({e.status_code}): {e.message}") from e
        u = resp.usage
        charged = LLMResult(
            output={},
            model=resp.model,
            input_tokens=u.input_tokens,
            cached_tokens=u.cache_read_input_tokens or 0,
            output_tokens=u.output_tokens,
            stop_reason=resp.stop_reason or "error",
            provider=self.name,
            request_ref=getattr(resp, "id", None),
        )
        if resp.stop_reason == "refusal":
            cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
            raise LLMRefusal(f"modelo recusou (categoria {cat})", usage=charged)
        if resp.stop_reason == "max_tokens":
            raise LLMTruncated("saída truncada por max_tokens", usage=charged)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            output = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("resposta não é JSON válido", usage=charged) from e
        return LLMResult(
            output=output,
            model=resp.model,
            input_tokens=u.input_tokens,
            cached_tokens=u.cache_read_input_tokens or 0,
            output_tokens=u.output_tokens,
            stop_reason=resp.stop_reason or "end_turn",
            provider=self.name,
            request_ref=getattr(resp, "_request_id", None),
        )


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_REFUSAL = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
GEMINI_TRANSIENT = {408, 429, 500, 502, 503, 504}


def _http_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # URL fixa https do Google
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


class GoogleLLMProvider:
    """Gemini API (generativelanguage.googleapis.com). Chave só via AIC_GOOGLE_API_KEY, enviada em cabeçalho
    (nunca na URL, para não aparecer em logs de proxy). Saída JSON validada pelo schema da tarefa."""

    name = "google"
    simulated = False

    def __init__(self, api_key: str | None = None, post=None, timeout: float = 180.0) -> None:
        key = api_key or (get_settings().google_api_key.get_secret_value() if get_settings().google_api_key else "")
        if not key:
            raise LLMError("AIC_GOOGLE_API_KEY ausente")
        self._key, self._post, self._timeout = key, post or _http_post, timeout

    def _body(self, req: LLMRequest, with_schema: bool) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "responseMimeType": "application/json",
            "maxOutputTokens": req.max_tokens,
            "temperature": 0.2,
            # raciocínio limitado e somado à reserva de custo (senão poderia ultrapassar o teto)
            "thinkingConfig": {"thinkingLevel": "low" if req.effort == "low" else "medium"}
            if req.model.startswith("gemini-3")
            else {"thinkingBudget": thinking_budget(req.model, req.effort)},
        }
        user = req.user
        if with_schema:
            cfg["responseJsonSchema"] = req.schema
        else:  # API sem suporte ao schema informado: o schema vai no texto e o código valida a saída mesmo assim
            user += "\n\nResponda SOMENTE com um objeto JSON válido conforme este JSON Schema:\n" + json.dumps(
                req.schema
            )
        return {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": cfg,
        }

    def complete(self, req: LLMRequest) -> LLMResult:
        url = GEMINI_URL.format(model=req.model)
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._key}
        try:
            status, data = self._post(url, headers, self._body(req, True), self._timeout)
            if status == 400 and "schema" in json.dumps(data).lower():
                status, data = self._post(url, headers, self._body(req, False), self._timeout)
        except (TimeoutError, OSError) as e:  # pode ter sido processada: custo incerto
            raise LLMTransient(f"{type(e).__name__}: {e}", uncertain=True) from e
        if status in GEMINI_TRANSIENT:
            raise LLMTransient(f"Gemini HTTP {status}: {(data.get('error') or {}).get('message', '')[:200]}")
        if status != 200:
            raise LLMError(f"Gemini HTTP {status}: {(data.get('error') or {}).get('message', '')[:200]}")
        u = data.get("usageMetadata") or {}
        cached = int(u.get("cachedContentTokenCount") or 0)
        charged = LLMResult(
            output={},
            model=data.get("modelVersion") or req.model,
            input_tokens=max(0, int(u.get("promptTokenCount") or 0) - cached),
            cached_tokens=cached,
            # tokens de "raciocínio" são cobrados como saída
            output_tokens=int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0),
            stop_reason="error",
            provider=self.name,
            request_ref=data.get("responseId"),
        )
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMRefusal(f"modelo recusou o pedido (bloqueio {block})", usage=charged)
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError("Gemini não devolveu candidatos", usage=charged)
        c = cands[0]
        reason = c.get("finishReason", "STOP")
        if reason in GEMINI_REFUSAL:
            raise LLMRefusal(f"modelo recusou (categoria {reason})", usage=charged)
        if reason == "MAX_TOKENS":
            raise LLMTruncated("saída truncada por max_tokens", usage=charged)
        text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
        try:
            output = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("resposta não é JSON válido", usage=charged) from e
        if not isinstance(output, dict):
            raise LLMError("resposta JSON não é um objeto", usage=charged)
        return LLMResult(
            output=output,
            model=charged.model,
            input_tokens=charged.input_tokens,
            cached_tokens=charged.cached_tokens,
            output_tokens=charged.output_tokens,
            stop_reason="end_turn",
            provider=self.name,
            request_ref=charged.request_ref,
        )


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        p = get_settings().llm_provider
        _provider = (
            AnthropicLLMProvider() if p == "anthropic" else GoogleLLMProvider() if p == "google" else MockLLMProvider()
        )
    return _provider


def set_provider(p: LLMProvider | None) -> None:
    """Para testes/avaliações offline."""
    global _provider
    _provider = p


# ───────────── Roteador ─────────────
@dataclass(frozen=True)
class Route:
    tier: str
    model: str
    reason: str


def route(*, spec_tier: str, risk: str = "LOW", attempt: int = 1) -> Route:
    s = get_settings()
    small, advanced = (
        (s.google_model_small, s.google_model_advanced)
        if s.llm_provider == "google"
        else (s.llm_model_small, s.llm_model_advanced)
    )
    if spec_tier == "advanced":
        return Route("advanced", advanced, "tarefa marcada como complexa/alto impacto")
    if risk in ("HIGH", "CRITICAL"):
        return Route("advanced", advanced, f"risco {risk}")
    if attempt > 1:
        return Route("advanced", advanced, "escalonado após saída inválida do modelo pequeno")
    return Route("small", small, "padrão: modelo pequeno")


def cost_micro_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> int:
    # versão com sufixo (ex.: gemini-2.5-flash-001) usa o preço da família; desconhecido = mais caro conhecido
    p = _price(model)
    return round(input_tokens * p["in"] + cached_tokens * p["cache_read"] + output_tokens * p["out"] + 0.4999)


# ───────────── Limites e orçamento ─────────────
@dataclass
class RunBudget:
    """Limites por execução vindos de agent_configs.limits."""

    max_tokens: int
    max_calls: int
    max_cost_micro_usd: int
    tokens: int = 0
    calls: int = 0
    cost_micro_usd: int = 0

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise LimitReached(f"limite de chamadas ({self.max_calls}) atingido")
        if self.tokens >= self.max_tokens:
            raise LimitReached(f"limite de tokens ({self.max_tokens}) atingido")
        if self.cost_micro_usd >= self.max_cost_micro_usd:
            raise LimitReached(f"limite de custo ({self.max_cost_micro_usd} µUS$) atingido")

    def add(self, r: LLMResult, cost: int) -> None:
        self.calls += 1
        self.tokens += r.input_tokens + r.cached_tokens + r.output_tokens
        self.cost_micro_usd += cost

    @classmethod
    def from_limits(cls, limits: dict[str, Any]) -> RunBudget:
        return cls(
            max_tokens=int(limits.get("max_tokens_per_run", 20000)),
            max_calls=int(limits.get("max_tool_calls", 12)),
            max_cost_micro_usd=int(limits.get("max_cost_micro_usd_per_run", 200_000)),
        )

    @classmethod
    def for_task(cls, limits: dict[str, Any], task_id) -> RunBudget:
        """Limites da tarefa contando o que ela JÁ gastou (auditoria A02): retomada após queda não zera o orçamento."""
        b = cls.from_limits(limits)
        if task_id is None:
            return b
        with session_scope() as db:
            calls, tokens, cost = db.execute(
                select(
                    func.count(AIUsage.id),
                    func.coalesce(func.sum(AIUsage.input_tokens + AIUsage.cached_tokens + AIUsage.output_tokens), 0),
                    func.coalesce(func.sum(AIUsage.request_cost_micro_usd), 0),
                ).where(AIUsage.task_id == task_id, AIUsage.stop_reason.not_in(("cache_hit", "LIMIT_REACHED")))
            ).one()
        b.calls, b.tokens, b.cost_micro_usd = int(calls), int(tokens), int(cost)
        return b


def month_spend_micro_usd(db, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(
        db.execute(
            select(func.coalesce(func.sum(AIUsage.request_cost_micro_usd), 0)).where(AIUsage.created_at >= start)
        ).scalar_one()
    )


def budget_remaining(db) -> int:
    return get_settings().ai_monthly_budget_micro_usd - month_spend_micro_usd(db)


# ───────────── Defesa contra prompt injection ─────────────
INJECTION_PATTERNS = [
    r"ignor(e|ar|e todas?|ar todas?)\s+(as\s+)?(instru[cç][oõ]es|previous|all|anteriores)",
    r"ignore (all|any|previous|the above)",
    r"you are now",
    r"voc[eê] agora [eé]",
    r"system prompt",
    r"</?\s*(system|fonte|instru)",
    r"aprove (imediatamente|esta|a proposta)",
    r"transfira|pix para|envie o token",
    r"desative (o|a) (pol[ií]tica|kill switch|aprova)",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.I)


def looks_like_injection(text: str) -> bool:
    return bool(_INJ.search(unicodedata.normalize("NFKC", text)))


def untrusted(source_id: str, title: str, text: str, meta: str = "") -> str:
    """Envolve conteúdo externo como DADO. Neutraliza tentativas de fechar a tag e caracteres de controle."""
    clean = "".join(ch for ch in unicodedata.normalize("NFKC", text) if ch in "\n\t" or ch.isprintable())
    clean = clean.replace("<", "‹").replace(">", "›")
    flag = ' alerta="possivel_injecao"' if looks_like_injection(text) else ""
    return f'<fonte id="{source_id}" confiavel="nao"{flag} titulo="{title[:120]}" {meta}>\n{clean}\n</fonte>'


# ───────────── Chamada com custo, cache e limites ─────────────
def _cache_key(provider: str, simulated: bool, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
    # procedência faz parte da chave: resposta do mock nunca é servida como se fosse do provedor real (auditoria A03)
    return sha256_of({"p": provider, "sim": simulated, "m": model, "s": system, "u": user, "f": schema})


MIN_OUTPUT_TOKENS = 256  # abaixo disso a resposta estruturada não cabe: melhor parar com LIMIT_REACHED
HOLD_TTL = dt.timedelta(minutes=10)  # reserva expira se o processo cair no meio da chamada


def _price(model: str) -> dict[str, float]:
    fam = max((k for k in PRICES if model == k or model.startswith(k + "-")), key=len, default=None)
    return PRICES[fam] if fam else PRICES["claude-opus-5-5"]


def _plan_output(budget: RunBudget, model: str, upper_in: int, max_tokens: int, thinking: int = 0) -> int:
    """Limites por execução ANTES da chamada (auditoria A02, critério de aceite da finalização): a saída máxima pedida
    é calculada para que o PIOR caso (entrada pelo limite superior + saída + raciocínio) caiba no saldo de tokens e
    de custo da execução. Se não couber uma resposta mínima, a chamada nem é feita."""
    p = _price(model)
    out = min(max_tokens, budget.max_tokens - budget.tokens - upper_in - thinking)
    if p["out"] > 0:
        spare = budget.max_cost_micro_usd - budget.cost_micro_usd - cost_micro_usd(model, upper_in, 0, thinking)
        out = min(out, int(spare / p["out"]) - 1)  # -1: margem do arredondamento de cost_micro_usd
    if out < MIN_OUTPUT_TOKENS:
        raise LimitReached(
            f"saldo da execução insuficiente para uma resposta (tokens {budget.tokens}/{budget.max_tokens}, "
            f"custo {budget.cost_micro_usd}/{budget.max_cost_micro_usd} µUS$)"
        )
    return out


def call_retrying(**kw: Any) -> tuple[LLMResult, Route, int]:
    """`call` com novas tentativas SÓ para erro temporário do provedor (LLMTransient). Erros definitivos, saída
    inválida e limite de orçamento sobem na hora. Cada tentativa é uma chamada completa (reserva, teto da execução e
    do mês, registro de uso), então as tentativas nunca furam o orçamento."""
    import time

    from aicommerce.config import get_settings

    s = get_settings()
    for n in range(s.llm_transient_retries + 1):
        try:
            return call(**kw)
        except LLMTransient:
            if n >= s.llm_transient_retries:
                raise
            time.sleep(s.llm_retry_backoff_s * (2**n))
    raise AssertionError("inalcançável")


def call(
    *,
    agent_id: str,
    purpose: str,
    spec_tier: str,
    risk: str,
    attempt: int,
    system: str,
    user: str,
    schema: dict[str, Any],
    mock_output: dict[str, Any] | None,
    budget: RunBudget,
    task_id=None,
    mission_id=None,
    cache_ttl: dt.timedelta | None = None,
    max_tokens: int = 4000,
    effort: str = "low",
) -> tuple[LLMResult, Route, int]:
    """Executa uma chamada roteada. Reserva o pior custo antes (teto mensal atômico), limita a saída ao saldo da
    execução, sem transação aberta durante a chamada à API; custo real gravado em seguida e a reserva liberada."""
    from sqlalchemy import text

    from aicommerce.models import AISpendHold

    r = route(spec_tier=spec_tier, risk=risk, attempt=attempt)
    provider = get_provider()
    budget.check()
    eff = "medium" if r.tier == "advanced" else effort
    upper_in = input_tokens_upper(system, user, schema)
    thinking = thinking_budget(r.model, eff)
    max_out = _plan_output(budget, r.model, upper_in, max_tokens, thinking)
    # pior caso REAL: entrada pelo limite superior + toda a saída permitida + raciocínio (Gemini)
    worst = cost_micro_usd(r.model, upper_in, 0, max_out + thinking)
    key = _cache_key(provider.name, provider.simulated, r.model, system, user, schema)
    now = dt.datetime.now(dt.UTC)
    with session_scope() as db:
        hit = db.get(LLMCache, key) if cache_ttl else None
        if hit and hit.expires_at > now and hit.provider == provider.name and hit.simulated == provider.simulated:
            result = LLMResult(
                output=hit.output,
                model=hit.model,
                input_tokens=0,
                cached_tokens=0,
                output_tokens=0,
                stop_reason="cache_hit",
                provider=hit.provider,
                cache_hit=True,
            )
            _record(db, agent_id, task_id, mission_id, purpose, r, result, 0, budget_remaining(db), hit.simulated)
            return result, r, 0
        # teto mensal: verificação + reserva atômicas (serializadas por advisory lock entre processos)
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('aic_ai_monthly_budget'))"))
        settle_expired_holds(db)  # reserva de processo que caiu vira custo antes de calcular o saldo
        held = db.execute(
            select(func.coalesce(func.sum(AISpendHold.amount_micro_usd), 0)).where(AISpendHold.expires_at > now)
        ).scalar_one()
        remaining = budget_remaining(db) - int(held)
        if remaining <= 0 or remaining < worst:
            raise LimitReached("orçamento mensal de IA esgotado ou insuficiente para esta chamada (ai_monthly_budget)")
        hold = AISpendHold(
            agent_id=agent_id,
            amount_micro_usd=worst,
            expires_at=now + HOLD_TTL,
            task_id=task_id,
            mission_id=mission_id,
            purpose=purpose[:64],
            provider=provider.name,
            simulated=provider.simulated,
        )
        db.add(hold)
        db.flush()
        hold_id = hold.id
    req = LLMRequest(
        model=r.model,
        system=system,
        user=user,
        schema=schema,
        mock_output=mock_output,
        max_tokens=max_out,
        effort=eff,
    )
    faults.point("llm.after_hold")
    try:
        result = provider.complete(req)
    except BaseException as e:
        # Falhou: o custo lançado depende do que se sabe. Resposta recebida → uso real informado pelo provedor;
        # incerto (timeout após envio ou erro inesperado) → pior caso reservado; erro antes de processar → nada.
        usage = getattr(e, "usage", None)
        charge_worst = getattr(e, "uncertain", False) or not isinstance(e, LLMError)
        with session_scope() as db:
            db.execute(AISpendHold.__table__.delete().where(AISpendHold.id == hold_id))
            if usage is not None or charge_worst:
                if usage is not None:
                    cost = cost_micro_usd(usage.model, usage.input_tokens, usage.cached_tokens, usage.output_tokens)
                    charged = LLMResult(**{**usage.__dict__, "stop_reason": getattr(e, "kind", "ERROR")})
                else:
                    cost = worst
                    charged = LLMResult(
                        output={},
                        model=r.model,
                        input_tokens=0,
                        cached_tokens=0,
                        output_tokens=0,
                        stop_reason="UNCERTAIN_" + getattr(e, "kind", "ERROR"),
                        provider=provider.name,
                    )
                budget.add(charged, cost)
                _record(
                    db,
                    agent_id,
                    task_id,
                    mission_id,
                    purpose,
                    r,
                    charged,
                    cost,
                    budget_remaining(db) - cost,
                    provider.simulated,
                )
        raise
    faults.point("llm.after_provider")  # provedor já cobrou; custo ainda não lançado
    cost = cost_micro_usd(result.model, result.input_tokens, result.cached_tokens, result.output_tokens)
    budget.add(result, cost)
    invalid = schema_errors(result.output, schema)  # validação geral do contrato da tarefa, antes de cache/uso
    with session_scope() as db:
        db.execute(AISpendHold.__table__.delete().where(AISpendHold.id == hold_id))
        _record(
            db, agent_id, task_id, mission_id, purpose, r, result, cost, budget_remaining(db) - cost, provider.simulated
        )
        if cache_ttl and not invalid:
            db.merge(
                LLMCache(
                    key_sha256=key,
                    model=result.model,
                    provider=provider.name,
                    simulated=provider.simulated,
                    output=result.output,
                    expires_at=dt.datetime.now(dt.UTC) + cache_ttl,
                )
            )
    faults.point("llm.after_record")
    if invalid:
        raise LLMOutputInvalid(invalid)
    return result, r, cost


def settle_expired_holds(db) -> int:
    """Reserva expirada = processo caiu durante a chamada: não dá para saber se o provedor cobrou. Lança o valor
    MÁXIMO reservado como custo da tarefa (stop_reason HOLD_EXPIRED) — o teto mensal nunca subconta (A02/A07)."""
    from aicommerce.models import AISpendHold

    now = dt.datetime.now(dt.UTC)
    expired = (
        db.execute(select(AISpendHold).where(AISpendHold.expires_at <= now).with_for_update(skip_locked=True))
        .scalars()
        .all()
    )
    for h in expired:
        db.add(
            AIUsage(
                agent_id=h.agent_id,
                task_id=h.task_id,
                mission_id=h.mission_id,
                purpose=h.purpose or "desconhecido",
                provider=h.provider or "desconhecido",
                model="desconhecido",
                tier="n/a",
                route_reason="processo caiu durante a chamada; custo máximo reservado foi contabilizado",
                input_tokens=0,
                cached_tokens=0,
                output_tokens=0,
                request_cost_micro_usd=h.amount_micro_usd,
                budget_remaining_micro_usd=budget_remaining(db) - h.amount_micro_usd,
                stop_reason="HOLD_EXPIRED",
                simulated=h.simulated,
            )
        )
        db.delete(h)
    return len(expired)


def _record(
    db, agent_id, task_id, mission_id, purpose, r: Route, res: LLMResult, cost: int, remaining: int, simulated: bool
) -> None:
    db.add(
        AIUsage(
            agent_id=agent_id,
            task_id=task_id,
            mission_id=mission_id,
            purpose=purpose,
            provider=res.provider,
            model=res.model,
            tier=r.tier,
            route_reason=r.reason,
            input_tokens=res.input_tokens,
            cached_tokens=res.cached_tokens,
            output_tokens=res.output_tokens,
            request_cost_micro_usd=cost,
            budget_remaining_micro_usd=remaining,
            stop_reason=res.stop_reason,
            cache_hit=res.cache_hit,
            simulated=simulated,
            request_ref=res.request_ref,
        )
    )


def record_limit_stop(*, agent_id: str, purpose: str, task_id=None, mission_id=None) -> None:
    """Registra stop_reason=LIMIT_REACHED no ledger (sem custo) para rastreabilidade."""
    with session_scope() as db:
        r = Route("n/a", "n/a", "limite atingido antes da chamada")
        res = LLMResult(
            output={},
            model="n/a",
            input_tokens=0,
            cached_tokens=0,
            output_tokens=0,
            stop_reason="LIMIT_REACHED",
            provider=get_provider().name,
        )
        _record(db, agent_id, task_id, mission_id, purpose, r, res, 0, budget_remaining(db), get_provider().simulated)
