"""Evidence source plan per agent (documented provider endpoints, never a fake connection).

'ready_in_code' means a read-only adapter exists, NOT that credentials are installed,
the API is reachable, or the channel/account is legally eligible to operate.
"""

from __future__ import annotations

SOURCE_CATALOG: dict[str, tuple[dict[str, str], ...]] = {
    "atlas": ({"source": "internal_checkpoint_ledger", "use": "workflow coordination", "status": "internal"},),
    "luna": (
        {
            "source": "mercadolivre_trends",
            "use": "weekly popular searches",
            "status": "ready_in_code",
            "url": "https://developers.mercadolivre.com.br/pt_br/tendencias",
        },
        {
            "source": "mercadolivre_highlights",
            "use": "category best sellers",
            "status": "requires_connector",
            "url": "https://developers.mercadolivre.com.br/pt_br/relatorios-de-faturamento/mais-vendidos-no-mercado-livre",
        },
        {
            "source": "brave_search",
            "use": "web research",
            "status": "ready_in_code",
            "url": "https://brave.com/pt-br/search/api/",
        },
    ),
    "nexus": (
        {
            "source": "brave_search",
            "use": "supplier prospects, not certification",
            "status": "ready_in_code",
            "url": "https://brave.com/pt-br/search/api/",
        },
        {
            "source": "receita_cnpj",
            "use": "supplier registration verification",
            "status": "requires_connector",
            "url": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos",
        },
        {"source": "internal_supplier_dossiers", "use": "supplier approval evidence", "status": "internal"},
    ),
    "dante": (
        {"source": "internal_pricing_engine", "use": "deterministic contribution margins", "status": "internal"},
        {
            "source": "mercadolivre_fees",
            "use": "actual marketplace fees by offer",
            "status": "requires_connector",
            "url": "https://developers.mercadolivre.com.br/pt_br",
        },
    ),
    "finn": (
        {"source": "internal_ledger", "use": "authoritative costs and reservations", "status": "internal"},
        {
            "source": "mercadopago_payments",
            "use": "real payment reconciliation",
            "status": "requires_credentials",
            "url": "https://www.mercadopago.com.br/developers/pt/reference/online-payments/subscriptions/search-payments/get",
        },
    ),
    "nova": (
        {"source": "channel_capabilities", "use": "internal channel eligibility gates", "status": "internal"},
        {
            "source": "mercadolivre_developers",
            "use": "official feature documentation",
            "status": "requires_connector",
            "url": "https://developers.mercadolivre.com.br/pt_br",
        },
        {
            "source": "brave_search",
            "use": "public policy discovery only, not approval",
            "status": "ready_in_code",
            "url": "https://brave.com/pt-br/search/api/",
        },
    ),
    "clara": (
        {"source": "internal_approved_kb", "use": "versioned compliance sources", "status": "internal"},
        {
            "source": "receita_dados",
            "use": "tax regulatory background",
            "status": "requires_connector",
            "url": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos",
        },
        {
            "source": "inmetro",
            "use": "product registration query",
            "status": "requires_connector",
            "url": "https://registro.inmetro.gov.br/consulta/",
        },
        {
            "source": "brave_search",
            "use": "find official pages, unreviewed until validated",
            "status": "ready_in_code",
            "url": "https://brave.com/pt-br/search/api/",
        },
    ),
    "vera": (
        {"source": "internal_listing_compliance", "use": "deterministic policy blockers", "status": "internal"},
        {
            "source": "mercadolivre_reviews",
            "use": "item ratings and reviews",
            "status": "requires_credentials",
            "url": "https://developers.mercadolivre.com.br/pt_br/opinioes-sobre-um-produto",
        },
    ),
    "iris": (
        {"source": "internal_product_attributes", "use": "substantiated product claims", "status": "internal"},
        {
            "source": "mercadolivre_trends",
            "use": "search language",
            "status": "requires_connector",
            "url": "https://developers.mercadolivre.com.br/pt_br/tendencias",
        },
    ),
    "eva": (
        {
            "source": "internal_orders_policies",
            "use": "customer orders and approved returns policy",
            "status": "internal",
        },
        {
            "source": "mercadolivre_questions",
            "use": "buyer questions on owned listings",
            "status": "requires_credentials",
            "url": "https://developers.mercadolivre.com.br/pt_br/realizacao-de-testes/gerenciamento-perguntas-respostas",
        },
    ),
    "logan": (
        {"source": "internal_fulfillment", "use": "actual order and dispatch states", "status": "internal"},
        {
            "source": "mercadolivre_shipping",
            "use": "quotes and shipment costs",
            "status": "requires_credentials",
            "url": "https://developers.mercadolivre.com.br/pt_br/mercadolideres-lojas-oficiais/mercado-envios-custos-e-cotacoes",
        },
        {
            "source": "melhor_envio",
            "use": "freight quotations",
            "status": "requires_credentials",
            "url": "https://docs.melhorenvio.com.br/docs/cotacao-de-fretes",
        },
    ),
    "orion": (
        {"source": "internal_analytics", "use": "authoritative aggregated KPIs", "status": "internal"},
        {
            "source": "mercadolivre_visits",
            "use": "traffic metrics for authorized listings",
            "status": "requires_credentials",
            "url": "https://developers.mercadolivre.com.br/pt_br/como-comecar/recurso-visits",
        },
    ),
    "pixel": (
        {"source": "internal_campaign_proposals", "use": "human-authorized spend", "status": "internal"},
        {
            "source": "google_ads",
            "use": "campaign performance metrics",
            "status": "requires_credentials",
            "url": "https://developers.google.com/google-ads/api/docs/get-started/onboarding",
        },
    ),
}


def sources_for(agent_id: str) -> tuple[dict[str, str], ...]:
    if agent_id not in SOURCE_CATALOG:
        raise ValueError("Agente desconhecido")
    return SOURCE_CATALOG[agent_id]
