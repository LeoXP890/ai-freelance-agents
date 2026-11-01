"""Configuração por ambiente com isolamento DRY_RUN × LIVE (REQ-DEV-004).

Regra: em DRY_RUN nenhuma credencial live pode existir no processo. Se existir, a aplicação
recusa iniciar — o erro é preferível a um efeito colateral acidental em provedor real.
"""

from __future__ import annotations

import enum
import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeMode(enum.StrEnum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class Environment(enum.StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


# Prefixos de variáveis que carregam credenciais de provedores reais. Nunca podem existir em DRY_RUN.
LIVE_SECRET_PREFIXES = (
    "LIVE_",
    "WOO_CONSUMER_",
    "ML_CLIENT_",
    "ML_REFRESH_",
    "SHOPEE_PARTNER_",
    "TTS_APP_",
    "FISCAL_PROVIDER_",
    "ADS_",
    "PAYMENT_",
)


SIM_WOO_WEBHOOK_SECRET = "sim-woo-webhook-secret-dryrun"
# Chave AES-256 de simulação (32 bytes em base64). Nunca usar fora de DRY_RUN.
SIM_PII_KEY = "c2ltLXBpaS1rZXktZHJ5cnVuLXNvbWVudGUtMzJieSE="
WEAK_SECRET_MARKERS = ("dev-only", "change-me", "test-secret", "__injetar")


class LiveCredentialsInDryRun(RuntimeError):
    pass


class PaidAIForbidden(RuntimeError):
    """IA paga (Gemini/Anthropic, embeddings) fora do lugar: CI nunca; local/teste só com autorização explícita."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_", env_file=None, extra="ignore")

    environment: Environment = Environment.LOCAL
    mode: RuntimeMode = RuntimeMode.DRY_RUN
    database_url: str = "postgresql+psycopg://aicommerce:aicommerce@localhost:55440/aicommerce"
    jwt_secret: str = Field(default="dev-only-change-me-dev-only-change-me", min_length=32)
    jwt_issuer: str = "aicommerce-api"
    access_token_ttl_seconds: int = 900
    mfa_challenge_ttl_seconds: int = 300
    mfa_enroll_ttl_seconds: int = 600
    # Navegador em dev + app desktop Tauri (macOS/Linux: tauri://localhost; Windows: http://tauri.localhost).
    # CORS não autoriza nada: toda chamada continua exigindo JWT/RBAC no backend.
    cors_origins: list[str] = ["http://localhost:3000", "tauri://localhost", "http://tauri.localhost"]
    # Seed de usuários de demonstração: somente permitido em DRY_RUN.
    seed_demo_users: bool = False
    office_poll_interval_ms: int = 400
    # Segredo do webhook WooCommerce. Em DRY_RUN usa um valor de simulação; em LIVE vem do secret manager.
    woo_webhook_secret: str = SIM_WOO_WEBHOOK_SECRET
    # Loja WooCommerce REAL (só LIVE): URL https + chave REST (WooCommerce → Configurações → Avançado → REST API,
    # permissão Leitura/Escrita). Nunca no código; o adaptador só envia com o gate CH-WOO aprovado.
    woo_base_url: str = ""
    woo_consumer_key: SecretStr | None = None
    woo_consumer_secret: SecretStr | None = None
    pii_key: str = SIM_PII_KEY
    pii_previous_keys: str = ""  # só durante a troca de chave (base64, separadas por vírgula); lidas para decifrar
    # application_id da aplicação no Mercado Livre (notificações são validadas por ele; ML não assina o corpo).
    ml_application_id: str = "SIM-ML-APP"
    # Aplicação REAL no DevCenter do ML (só LIVE): client_id = application_id; segredo nunca no código; redirect https
    # exatamente igual ao cadastrado (ex.: https://api.sualoja.com.br/api/v1/channels/MERCADO_LIVRE/oauth/callback).
    ml_client_secret: SecretStr | None = None
    ml_redirect_uri: str = ""
    # Endereço público da API (o mesmo que o app usa), base das URLs das fotos que os canais baixam. LIVE: https.
    public_base_url: str = "http://localhost:8080/api"
    metrics_token: str = "sim-metrics-token-dryrun"  # scrape do Prometheus; LIVE exige valor real
    # IA: "mock" (padrão, sem rede/custo), "anthropic" (Claude API; credencial resolvida pelo SDK) ou
    # "google" (Gemini API; chave em AIC_GOOGLE_API_KEY — nunca no código nem em log).
    research_trend_source: Literal["mock", "mercadolivre"] = "mock"  # read-only; no sales or account actions
    research_ml_access_token: SecretStr | None = None  # token próprio para GET de trends; sem ações comerciais
    research_web_source: Literal["none", "brave", "gemini"] = "none"  # opt-in; gemini usa a mesma chave do Google
    research_brave_api_key: SecretStr | None = None  # use secret store; jamais registrar em log
    research_me_sandbox_token: SecretStr | None = None  # sandbox de cotação; não é credencial LIVE
    research_me_user_agent: str = ""  # exige contato técnico no padrão Melhor Envio
    # Teto e ritmo das pesquisas externas (ai/research_guard.py). Brave é PAGO: sem teto mensal (> 0) não consulta.
    research_brave_monthly_cap: int = 0
    research_ml_trends_daily_cap: int = 300
    research_me_sandbox_daily_cap: int = 100
    # Google Search pelo Gemini 3: 5.000 buscas/mês grátis (cobrança por busca, uma pergunta pode gerar várias).
    # Cofre do OAuth somente leitura do Mercado Livre neste servidor (ferramentas dos agentes); vazio = desligado.
    ml_readonly_vault: str | None = None
    # Sessão do proprietário no Dropi (cookies, 0600), salva por scripts/dropi_login.py; vazio = desligado.
    dropi_session_file: str | None = None
    # CJdropshipping (só leitura): chave e arquivo do token de 180 dias (0600, fora do Git).
    cj_api_key: SecretStr | None = None
    cj_token_file: str | None = None
    # Fotos de tela do robô de publicação (evidência de cada passo).
    robot_evidence_dir: str | None = None
    # Lucro mínimo por venda para uma publicação ir à aprovação do proprietário (centavos).
    platform_min_profit_minor: int = 1000
    research_gemini_daily_cap: int = 30  # ~4 buscas por pergunta → ~3.600/mês, dentro da franquia
    research_gemini_model: str = "gemini-3.8-flash"
    research_min_interval_s: float = 1.0  # entre chamadas ao mesmo provedor (todos os processos: conta no banco)
    llm_provider: Literal["mock", "anthropic", "google"] = "mock"
    google_api_key: SecretStr | None = None
    google_model_small: str = "gemini-3.8-flash"  # 2.5 não está mais disponível para chaves novas (31/10/2026)
    google_model_advanced: str = "gemini-3.1-pro-preview"
    llm_model_small: str = "claude-haiku-5-5"  # SDD §12: modelo pequeno por padrão
    llm_model_advanced: str = "claude-opus-5-5"  # escalonamento p/ casos complexos e alto risco
    # Base de conhecimento: "hashing" (padrão; vetor lexical, sem rede/custo) ou "google" (embeddings semânticos do
    # Gemini; custo entra no mesmo teto mensal de IA). Trocar exige `manage reindex-kb` (a busca não mistura espaços).
    kb_embedder: Literal["hashing", "google"] = "hashing"
    google_embedding_model: str = "gemini-embedding-001"
    # erro temporário do provedor (429/5xx/timeout): novas tentativas com espera crescente; cada uma passa pelo
    # orçamento e pela reserva de novo, e a que falhou continua cobrada (pior caso quando incerta)
    llm_transient_retries: int = 2
    llm_retry_backoff_s: float = 1.0
    ai_monthly_budget_micro_usd: int = 20_000_000  # US$ 20/mês; acima disso as chamadas param (LIMIT_REACHED)
    # Desenvolvimento usa IA SIMULADA. Provedor pago em ambiente local/teste exige este "sim" explícito do
    # proprietário (custa créditos dele); em CI é proibido mesmo com ele.
    allow_paid_ai: bool = False
    # Pagamento ao fornecedor (finance/funding.py): leitura de saldo mais antiga que isto não financia nada.
    funding_balance_max_age_s: int = Field(default=900, gt=0)
    # Códigos das fontes de caixa (cash_sources). Vazio = executor de SUPPLIER_PURCHASE não exige caixa (só orçamento).
    funding_operating_source: str = ""
    funding_reserve_source: str = ""

    @field_validator(
        "woo_consumer_key",
        "woo_consumer_secret",
        "ml_client_secret",
        "google_api_key",
        "research_ml_access_token",
        "research_brave_api_key",
        "research_me_sandbox_token",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        # o compose repassa variável ausente como "" — vazio significa "não configurado", nunca uma credencial
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def uses_paid_ai(self) -> bool:
        return self.llm_provider != "mock" or self.kb_embedder != "hashing" or self.research_web_source == "gemini"

    @model_validator(mode="after")
    def _guard(self) -> Settings:
        if self.llm_provider == "google" and not (
            self.google_api_key and self.google_api_key.get_secret_value().strip()
        ):
            raise ValueError("llm_provider=google exige AIC_GOOGLE_API_KEY")
        if self.research_web_source == "gemini" and not (
            self.google_api_key and self.google_api_key.get_secret_value().strip()
        ):
            raise ValueError("research_web_source=gemini exige AIC_GOOGLE_API_KEY")
        if self.kb_embedder == "google" and not (
            self.google_api_key and self.google_api_key.get_secret_value().strip()
        ):
            raise ValueError("kb_embedder=google exige AIC_GOOGLE_API_KEY")
        if self.mode is RuntimeMode.DRY_RUN and (self.woo_consumer_key or self.woo_consumer_secret):
            raise LiveCredentialsInDryRun(
                "credenciais reais do WooCommerce presentes em DRY_RUN (proibido, REQ-DEV-004)"
            )
        if self.environment is Environment.PRODUCTION and not self.public_base_url.startswith("https://"):
            raise ValueError("AIC_PUBLIC_BASE_URL precisa ser https:// em produção (canais baixam as fotos por ela)")
        if self.uses_paid_ai:
            if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
                raise PaidAIForbidden("CI nunca usa IA paga: AIC_LLM_PROVIDER=mock e AIC_KB_EMBEDDER=hashing")
            if self.environment in (Environment.LOCAL, Environment.TEST) and not self.allow_paid_ai:
                raise PaidAIForbidden(
                    "IA paga fora de produção consome créditos do proprietário: exige AIC_ALLOW_PAID_AI=true"
                )
        if self.mode is RuntimeMode.DRY_RUN and self.ml_client_secret:
            raise LiveCredentialsInDryRun("segredo real da aplicação Mercado Livre presente em DRY_RUN (REQ-DEV-004)")
        if self.ml_redirect_uri and not self.ml_redirect_uri.startswith("https://") and not self.is_dry_run:
            raise ValueError("AIC_ML_REDIRECT_URI precisa ser https://")
        if self.woo_base_url and not self.woo_base_url.startswith("https://") and self.mode is not RuntimeMode.DRY_RUN:
            raise ValueError("AIC_WOO_BASE_URL da loja real precisa ser https://")
        if self.mode is RuntimeMode.DRY_RUN:
            leaked = sorted(k for k in os.environ if k.startswith(LIVE_SECRET_PREFIXES))
            if leaked:
                raise LiveCredentialsInDryRun(
                    f"Credenciais live presentes em DRY_RUN (proibido, REQ-DEV-004): {', '.join(leaked)}"
                )
        else:
            if self.seed_demo_users:
                raise ValueError("seed_demo_users é proibido fora de DRY_RUN")
            if self._weak_secret():
                raise ValueError("jwt_secret de desenvolvimento/teste é proibido em LIVE")
            if self.woo_webhook_secret == SIM_WOO_WEBHOOK_SECRET:
                raise ValueError("woo_webhook_secret de simulação é proibido em LIVE")
            if self.pii_key == SIM_PII_KEY:
                raise ValueError("pii_key de simulação é proibida em LIVE")
            if self.metrics_token.startswith("sim-"):
                raise ValueError("metrics_token de simulação é proibido em LIVE")
        if self.environment is Environment.PRODUCTION and self._weak_secret():
            raise ValueError("jwt_secret de desenvolvimento/teste é proibido em produção")
        return self

    def _weak_secret(self) -> bool:
        return any(marker in self.jwt_secret.lower() for marker in WEAK_SECRET_MARKERS)

    @property
    def is_dry_run(self) -> bool:
        return self.mode is RuntimeMode.DRY_RUN


@lru_cache
def get_settings() -> Settings:
    return Settings()
