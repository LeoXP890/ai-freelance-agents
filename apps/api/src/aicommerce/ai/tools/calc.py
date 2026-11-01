"""Ferramentas de cálculo (código puro, zero token): lucro, preço para um lucro alvo e custo máximo de anúncio."""

from __future__ import annotations

from typing import Any

from aicommerce.ai.tools import Tool, ToolError, register

COMMON = {
    "cost": "custo do produto no fornecedor (R$)",
    "fee_pct": "tarifa percentual do canal (ex.: 12.5)",
    "fee_fixed": "tarifa fixa por venda (R$)",
    "shipping": "frete por conta do vendedor (R$)",
}


def _num(args: dict[str, Any], k: str, default: float = 0.0) -> float:
    v = float(args.get(k, default))
    if v < 0 or v > 1_000_000:
        raise ToolError(f"{k} fora da faixa")
    return v


def profit(args: dict[str, Any]) -> dict[str, Any]:
    price, cost = _num(args, "price"), _num(args, "cost")
    fee = round(price * _num(args, "fee_pct") / 100 + _num(args, "fee_fixed"), 2)
    ship, ads = _num(args, "shipping"), _num(args, "ads")
    p = round(price - fee - ship - cost - ads, 2)
    return {
        "price": price,
        "fee": fee,
        "shipping": ship,
        "cost": cost,
        "ads": ads,
        "profit": p,
        "margin_pct": round(p / price * 100, 1) if price else None,
    }


def price_for_profit(args: dict[str, Any]) -> dict[str, Any]:
    pct = _num(args, "fee_pct")
    if pct >= 100:
        raise ToolError("tarifa percentual precisa ser menor que 100")
    base = _num(args, "cost") + _num(args, "shipping") + _num(args, "fee_fixed") + _num(args, "target_profit")
    price = round(base / (1 - pct / 100) + 0.0049, 2)
    return {"price": price, "check": profit({**args, "price": price})}


def max_cpa(args: dict[str, Any]) -> dict[str, Any]:
    r = profit(args)
    return {
        "max_ad_cost_per_sale": round(max(0.0, r["profit"] - _num(args, "min_profit")), 2),
        "profit_without_ads": r["profit"],
    }


register(
    Tool(
        "calc.profit",
        "Lucro por venda",
        "Preço − tarifa − frete − custo − anúncio = lucro e margem.",
        {"price": "preço de venda (R$)", **COMMON, "ads": "custo de anúncio por venda (R$)"},
        profit,
        required=("price", "cost"),
        source="cálculo",
    )
)
register(
    Tool(
        "calc.price_for_profit",
        "Preço para um lucro alvo",
        "Menor preço que deixa o lucro desejado.",
        {**COMMON, "target_profit": "lucro desejado por venda (R$)"},
        price_for_profit,
        required=("cost", "target_profit"),
        source="cálculo",
    )
)
register(
    Tool(
        "calc.max_cpa",
        "Máximo de anúncio por venda",
        "Quanto dá para pagar de anúncio por venda mantendo o lucro mínimo.",
        {"price": "preço de venda (R$)", **COMMON, "min_profit": "lucro mínimo por venda (R$)"},
        max_cpa,
        required=("price", "cost"),
        source="cálculo",
    )
)
