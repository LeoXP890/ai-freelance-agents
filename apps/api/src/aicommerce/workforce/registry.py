"""Registro dos 13 papéis lógicos (SDD §7) com configuração versionada inicial (FR-002).

Fase A: identidade, sala, ferramentas permitidas e limites. Os prompts/objetivos detalhados e o adapter
LLM entram em AI-001/AI-002. Nenhum agente recebe ferramenta de alteração externa direta: tudo que
muda estado externo passa por `governance.propose` (ActionProposal tipada).
"""

from __future__ import annotations

from typing import Any

ROOMS = ["ceo", "research", "supply", "marketing", "store", "finance", "support", "compliance", "meeting"]

DEFAULT_LIMITS: dict[str, Any] = {
    "max_tokens_per_run": 20000,
    "max_tool_calls": 12,
    "max_handoffs": 3,
    "max_cost_minor_per_mission": 500,
    "max_runtime_seconds": 300,
    # v2: custo de IA em micro-dólares (FR-017) e limite de fan-out das missões
    "max_cost_micro_usd_per_run": 200_000,
    "max_mission_steps": 4,
}

AGENTS: list[dict[str, Any]] = [
    {
        "id": "atlas",
        "name": "Atlas",
        "role": "Direção e planejamento de missões",
        "room": "ceo",
        "color": "#8e75fb",
        "tools": ["plan.mission", "governance.propose", "kb.search", "trends.seasonal", "trends.ml_searches"],
    },
    {
        "id": "dante",
        "name": "Dante",
        "role": "Modelagem comercial de dropshipping",
        "room": "meeting",
        "color": "#f08a5d",
        "tools": [
            "kb.search",
            "finance.margin_calc",
            "governance.propose",
            "ml.fees",
            "ml.shipping_cost",
            "ml.match_gtin",
        ],
    },
    {
        "id": "luna",
        "name": "Luna",
        "role": "Pesquisa de mercado e tendências",
        "room": "research",
        "color": "#2bc1b3",
        "tools": [
            "trends.search",
            "kb.search",
            "evidence.record",
            "web.search",
            "trends.ml_searches",
            "trends.ml_bestsellers",
            "trends.google_daily",
            "trends.google_search",
            "trends.seasonal",
            "dropi.search",
            "cj.search",
            "cj.freight",
            "fx.usd_brl",
            "market.price_check",
            "ml.match_title",
            "ml.category_box",
            "ml.match_gtin",
            "ml.fees",
            "ml.shipping_cost",
            "ml.category_predict",
        ],
    },
    {
        "id": "nexus",
        "name": "Nexus",
        "role": "Fornecedores e ofertas",
        "room": "supply",
        "color": "#4ea8de",
        "tools": [
            "suppliers.search",
            "dropi.search",
            "cj.search",
            "cj.freight",
            "fx.usd_brl",
            "suppliers.dossier",
            "evidence.record",
            "governance.propose",
            "web.search",
            "ml.match_gtin",
        ],
    },
    {
        "id": "iris",
        "name": "Iris",
        "role": "Criativos e conteúdo",
        "room": "marketing",
        "color": "#f25f9c",
        "tools": ["media.draft", "kb.search", "ml.category_attributes"],
    },
    {
        "id": "pixel",
        "name": "Pixel",
        "role": "Mídia paga",
        "room": "marketing",
        "color": "#ffb703",
        "tools": ["ads.read_metrics", "governance.propose", "trends.seasonal"],
    },
    {
        "id": "nova",
        "name": "Nova",
        "role": "Loja e canais",
        "room": "store",
        "color": "#06d6a0",
        "tools": [
            "channels.capabilities",
            "ml.my_listing_find",
            "listings.prepare",
            "governance.propose",
            "web.search",
            "ml.category_predict",
            "ml.category_attributes",
            "ml.fees",
        ],
    },
    {
        "id": "finn",
        "name": "Finn",
        "role": "Finanças, custos e ledger",
        "room": "finance",
        "color": "#90be6d",
        "tools": ["finance.overview", "finance.margin_calc", "analytics.query", "ml.fees"],
    },
    {
        "id": "eva",
        "name": "Eva",
        "role": "Atendimento ao cliente",
        "room": "support",
        "color": "#ff8fab",
        "tools": ["orders.read", "kb.search"],
    },
    {
        "id": "orion",
        "name": "Orion",
        "role": "Analytics e experimentos",
        "room": "finance",
        "color": "#577590",
        "tools": ["analytics.query", "kb.search"],
    },
    {
        "id": "clara",
        "name": "Clara",
        "role": "Compliance jurídico-fiscal (aponta fontes, escalona)",
        "room": "compliance",
        "color": "#c77dff",
        "tools": ["kb.search", "gates.read", "web.search"],
    },
    {
        "id": "logan",
        "name": "Logan",
        "role": "Logística e SLAs",
        "room": "store",
        "color": "#adb5bd",
        "tools": ["fulfillment.track", "orders.read", "analytics.query", "shipping.quote_sandbox", "ml.shipping_cost"],
    },
    {
        "id": "vera",
        "name": "Vera",
        "role": "Qualidade e conversão",
        "room": "research",
        "color": "#e9c46a",
        "tools": ["listings.audit", "kb.search", "ml.category_attributes", "ml.category_predict"],
    },
]

OUTPUT_SCHEMA = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}
