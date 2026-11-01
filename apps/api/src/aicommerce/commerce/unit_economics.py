"""Quanto sobra por venda (preço − tarifas − fretes − custo), conferido nas fontes ANTES de pedir aprovação.

Regras do Mercado Livre (Brasil) usadas aqui, sempre lidas da API na hora (ml.fees / ml.shipping_cost):
- tarifa de venda = percentual da categoria + tarifa fixa (a API devolve o total em `sale_fee`, que já inclui a fixa
  cobrada abaixo de R$ 79);
- a partir de R$ 79 o frete grátis é obrigatório e uma parte é paga pelo vendedor (custo do Mercado Envios);
  abaixo disso quem paga o frete é o comprador.
O frete do fornecedor (Dropi) entra sempre: o lojista paga ao fornecedor por pedido.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

FREE_SHIPPING_FROM = 79.0


class EconomicsError(Exception):
    pass


def _line(key: str, label: str, amount: float, source: str) -> dict[str, Any]:
    return {"key": key, "label": label, "amount_minor": round(amount * 100), "source": source}


def compute(
    *,
    price: float,
    cost: float,
    fee_total: float,
    fee_pct: float | None,
    fee_fixed: float | None,
    supplier_freight: float,
    seller_shipping: float,
    sources: dict[str, str],
) -> dict[str, Any]:
    """Conta exata por venda; valores em reais na entrada, centavos na saída."""
    pct_part = round(price * (fee_pct or 0) / 100, 2) if fee_pct is not None else fee_total
    fixed_part = round(fee_total - pct_part, 2) if fee_pct is not None else 0.0
    lines = [
        _line("price", "Preço de venda", price, "aprovação"),
        _line(
            "fee_pct",
            f"Tarifa do Mercado Livre ({fee_pct:g}%)" if fee_pct is not None else "Tarifa do Mercado Livre",
            -pct_part,
            sources["fee"],
        ),
    ]
    if fixed_part > 0.004:
        lines.append(_line("fee_fixed", "Tarifa fixa do Mercado Livre (abaixo de R$ 79)", -fixed_part, sources["fee"]))
    lines += [
        _line(
            "seller_shipping",
            "Frete grátis por sua conta (Mercado Envios)"
            if price >= FREE_SHIPPING_FROM
            else "Frete (pago pelo comprador abaixo de R$ 79)",
            -seller_shipping,
            sources["shipping"],
        ),
        _line(
            "supplier_freight", "Frete/envio cobrado pelo fornecedor", -supplier_freight, sources["supplier_freight"]
        ),
        _line("cost", "Custo do produto no fornecedor", -cost, sources["cost"]),
    ]
    profit = round(sum(line["amount_minor"] for line in lines))
    return {
        "lines": lines,
        "profit_minor": profit,
        "margin_pct": round(profit / (price * 100) * 100, 1) if price else None,
        "currency": "BRL",
    }


def for_listing(
    db: Session,
    *,
    agent_id: str,
    title: str,
    price: float,
    cost: float,
    supplier_freight: float | None,
    category_id: str | None = None,
    box: dict[str, Any] | None = None,
    known_seller_shipping: float | None = None,
) -> dict[str, Any]:
    """Confere tarifa e frete no Mercado Livre (ferramentas com cache) e devolve a conta completa."""
    from aicommerce.ai import tools

    if supplier_freight is None:
        raise EconomicsError("frete do fornecedor desconhecido: confira no Dropi antes de propor")
    try:
        if not category_id:
            category_id = tools.call(db, agent_id, "ml.category_predict", {"title": title})[0]["category_id"]
        fee = tools.call(db, agent_id, "ml.fees", {"price": price, "category_id": category_id})
        if price < FREE_SHIPPING_FROM:
            ship, ship_src = 0.0, "regra do Mercado Livre: abaixo de R$ 79 o comprador paga o frete"
        elif box and len(box.get("cm") or []) == 3 and (box.get("grams") or 0) > 0:
            cm, grams = box["cm"], box["grams"]
            ship = tools.call(
                db,
                agent_id,
                "ml.shipping_cost",
                {"height": cm[0], "width": cm[1], "length": cm[2], "grams": grams, "price": price},
            )["seller_cost"]
            ship_src = "API do Mercado Livre (frete do vendedor)"
        elif known_seller_shipping is not None:
            ship, ship_src = float(known_seller_shipping), "cotação anterior do Mercado Livre (sem medidas novas)"
        else:
            raise EconomicsError("sem medidas da caixa para cotar o frete do Mercado Envios")
    except (tools.ToolError, IndexError, KeyError) as exc:
        raise EconomicsError(f"não foi possível conferir no Mercado Livre: {exc}") from None
    out = compute(
        price=price,
        cost=cost,
        fee_total=float(fee["sale_fee"]),
        fee_pct=fee.get("percentage_fee"),
        fee_fixed=fee.get("fixed_fee"),
        supplier_freight=float(supplier_freight),
        seller_shipping=float(ship),
        sources={
            "fee": "API do Mercado Livre (listing_prices)",
            "shipping": ship_src,
            "supplier_freight": "Dropi (cálculo de frete do fornecedor)",
            "cost": "Dropi (preço do fornecedor)",
        },
    )
    return {**out, "category_id": category_id}
