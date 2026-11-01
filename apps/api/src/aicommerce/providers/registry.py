"""Resolve ação → (provedor, operação). DRY_RUN usa Mock*. LIVE usa o cliente real do canal (WooCommerce, Mercado
Livre), que só fica SUPPORTED com credencial/conta real E gate aprovado; sem isso o executor falha tipado."""

from __future__ import annotations

from dataclasses import dataclass

from aicommerce.providers.contracts import Capability, ExternalProvider
from aicommerce.providers.marketplaces import CONTRACT_STATUS, MLRestProvider, MockMarketplaceProvider
from aicommerce.providers.mock import MockAdsProvider, MockSupplierProvider
from aicommerce.providers.woocommerce import MockWooCommerceProvider, WooCommerceRestProvider

ACTION_OPERATION: dict[str, tuple[str, str]] = {
    # action_type: (família, operação)
    "PUBLISH_LISTING": ("commerce", "publish_listing"),
    "UPDATE_LISTING_MATERIAL": ("commerce", "update_listing"),
    "PAUSE_LISTING": ("commerce", "pause_listing"),
    "CREATE_CAMPAIGN": ("ads", "create_campaign"),
    "INCREASE_BUDGET": ("ads", "increase_budget"),
    "PAUSE_MEDIA": ("ads", "pause_campaign"),
    "REFUND": ("commerce", "refund_order"),
    "SUPPLIER_PURCHASE": ("supplier", "place_order"),
}

CHANNELS = ("WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP")


@dataclass(frozen=True)
class Resolution:
    provider: ExternalProvider | None
    operation: str | None
    capability: Capability
    reason: str


def resolve(
    action_type: str,
    mode: str,
    channel: str | None,
    approved_gates: frozenset[str] = frozenset(),
    live_accounts: frozenset[str] = frozenset(),
) -> Resolution:
    mapping = ACTION_OPERATION.get(action_type)
    if mapping is None:
        return Resolution(None, None, Capability.UNAVAILABLE, f"ação {action_type} sem adaptador implementado")
    family, op = mapping
    if mode != "DRY_RUN":
        if family == "commerce" and channel in ("WOOCOMMERCE", "MERCADO_LIVRE"):
            if channel == "WOOCOMMERCE":
                woo = WooCommerceRestProvider(gate_approved=WooCommerceRestProvider.GATE in approved_gates)
                cap = woo.capability(op, mode)
                why = {
                    Capability.SUPPORTED: "loja WooCommerce real configurada e gate CH-WOO aprovado",
                    Capability.BLOCKED_BY_POLICY: "gate CH-WOO não aprovado pelo proprietário",
                }.get(cap, "loja WooCommerce real não configurada (AIC_WOO_BASE_URL/CONSUMER_KEY/SECRET)")
                return Resolution(woo, op, cap, why)
            from aicommerce.commerce.channels import live_token

            ml = MLRestProvider(
                gate_approved=MLRestProvider.GATE in approved_gates,
                connected=channel in live_accounts,
                token_source=lambda: live_token(channel),
            )
            cap = ml.capability(op, mode)
            why = {
                Capability.SUPPORTED: "conta Mercado Livre real conectada e gate CH-ML aprovado",
                Capability.BLOCKED_BY_POLICY: "gate CH-ML não aprovado pelo proprietário",
            }.get(cap, "nenhuma conta Mercado Livre real conectada (Canais → Conectar conta)")
            return Resolution(ml, op, cap, why)
        return Resolution(None, op, Capability.UNAVAILABLE, "nenhum provedor real homologado (LIVE)")
    provider: ExternalProvider
    if family == "commerce":
        provider = (
            MockWooCommerceProvider()
            if (channel or "WOOCOMMERCE") == "WOOCOMMERCE"
            else MockMarketplaceProvider(channel)
        )
    elif family == "supplier":
        provider = MockSupplierProvider()
    else:
        provider = MockAdsProvider()
    return Resolution(provider, op, provider.capability(op, mode), "mock DRY_RUN")


def channel_capabilities(channel: str, mode: str) -> dict[str, str]:
    """ChannelCapabilityMatrix por canal (SDD §23.3). DRY_RUN = mocks; LIVE = cliente real ainda não homologado."""
    live_real = channel in ("WOOCOMMERCE", "MERCADO_LIVRE")
    out: dict[str, str] = {}
    for op in ("publish_listing", "update_listing", "pause_listing"):
        if mode == "DRY_RUN":
            out[op] = Capability.SUPPORTED.value
        else:
            out[op] = (Capability.UNVERIFIED if live_real else Capability.UNAVAILABLE).value
    webhooks = channel in ("WOOCOMMERCE", "MERCADO_LIVRE")
    out["ack_webhook"] = (
        Capability.SUPPORTED
        if webhooks and mode == "DRY_RUN"
        else Capability.UNVERIFIED
        if webhooks
        else Capability.UNAVAILABLE
    ).value
    out["fetch_orders"] = out["ack_webhook"]
    out["estimate_fees"] = Capability.UNVERIFIED.value  # tabela parametrizada com valores fictícios
    out["fetch_settlements"] = (Capability.SUPPORTED if mode == "DRY_RUN" else Capability.UNAVAILABLE).value
    if channel == "TIKTOK_SHOP":
        out["cross_border_fulfillment"] = Capability.BLOCKED_BY_POLICY.value
    out["contract_status"] = CONTRACT_STATUS.get(channel, "VERIFIED_SUBSET")
    return out
