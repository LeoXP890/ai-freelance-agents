"""Margem de contribuição e preço por canal (SDD FR-007, §12 "SQL/código primeiro para números").

Aritmética inteira: dinheiro em minor units (centavos) e percentuais em basis points (1 bps = 0,01%).
Custos variáveis incidem sobre o preço (comissão, meio de pagamento, tributo, provisão de devolução);
custos fixos por unidade são somados (custo do fornecedor, frete, tarifa fixa do canal, CPA de mídia).

    contribuição(P) = P − ⌈P·Σbps/10000⌉ − fixos
    breakeven       = menor P com contribuição ≥ 0
    preço p/ alvo   = menor P com contribuição ≥ ⌈P·alvo_bps/10000⌉

Os parâmetros fiscais são insumos: este módulo não decide regime tributário (SDD §10).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

BPS = 10_000


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def pct_of(amount_minor: int, bps: int) -> int:
    """Parcela percentual arredondada para cima (custo nunca subestimado)."""
    return _ceil_div(amount_minor * bps, BPS)


@dataclass(frozen=True)
class PricingInput:
    cost_minor: int
    shipping_minor: int
    channel_fixed_fee_minor: int
    ads_cpa_minor: int
    commission_bps: int
    payment_fee_bps: int
    tax_bps: int
    returns_bps: int
    target_margin_bps: int
    fee_schedule_verified: bool
    tax_approved: bool

    def __post_init__(self) -> None:
        for name in (
            "cost_minor",
            "shipping_minor",
            "channel_fixed_fee_minor",
            "ads_cpa_minor",
            "commission_bps",
            "payment_fee_bps",
            "tax_bps",
            "returns_bps",
            "target_margin_bps",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} não pode ser negativo")

    @property
    def variable_bps(self) -> int:
        return self.commission_bps + self.payment_fee_bps + self.tax_bps + self.returns_bps

    @property
    def fixed_minor(self) -> int:
        return self.cost_minor + self.shipping_minor + self.channel_fixed_fee_minor + self.ads_cpa_minor


@dataclass(frozen=True)
class PriceBreakdown:
    price_minor: int
    commission_minor: int
    payment_fee_minor: int
    tax_minor: int
    returns_minor: int
    fixed_minor: int
    contribution_minor: int
    margin_bps: int  # contribuição / preço

    @property
    def variable_minor(self) -> int:
        return self.commission_minor + self.payment_fee_minor + self.tax_minor + self.returns_minor


@dataclass(frozen=True)
class Quote:
    breakdown: PriceBreakdown | None
    breakeven_minor: int | None
    suggested_minor: int | None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def breakdown(inp: PricingInput, price_minor: int) -> PriceBreakdown:
    if price_minor <= 0:
        raise ValueError("preço deve ser > 0")
    commission = pct_of(price_minor, inp.commission_bps)
    payment = pct_of(price_minor, inp.payment_fee_bps)
    tax = pct_of(price_minor, inp.tax_bps)
    returns = pct_of(price_minor, inp.returns_bps)
    contribution = price_minor - commission - payment - tax - returns - inp.fixed_minor
    margin = (contribution * BPS) // price_minor
    return PriceBreakdown(price_minor, commission, payment, tax, returns, inp.fixed_minor, contribution, margin)


def _min_price(inp: PricingInput, extra_bps: int) -> int | None:
    """Menor P inteiro com P − Σ⌈P·bps_i⌉ − fixos ≥ ⌈P·extra⌉. None se impossível."""
    total = inp.variable_bps + extra_bps
    if total >= BPS:
        return None
    # Estimativa contínua e ajuste fino por causa dos arredondamentos para cima de cada parcela.
    p = max(1, _ceil_div(inp.fixed_minor * BPS, BPS - total))
    while p > 1:
        b = breakdown(inp, p - 1)
        if b.contribution_minor >= pct_of(p - 1, extra_bps):
            p -= 1
        else:
            break
    while True:
        b = breakdown(inp, p)
        if b.contribution_minor >= pct_of(p, extra_bps):
            return p
        p += 1


def quote(inp: PricingInput, price_minor: int | None = None) -> Quote:
    warnings: list[str] = []
    if not inp.fee_schedule_verified:
        warnings.append("Tarifas do canal FICTÍCIAS/não verificadas: conferir tabela oficial antes de operar.")
    if not inp.tax_approved:
        warnings.append("Alíquota tributária não aprovada por contador: cálculo apenas indicativo (FISCAL_HOLD).")
    breakeven = _min_price(inp, 0)
    suggested = _min_price(inp, inp.target_margin_bps)
    if breakeven is None:
        warnings.append("Custos variáveis ≥ 100% do preço: nenhum preço cobre os custos.")
    elif suggested is None:
        warnings.append("Margem-alvo inatingível com os custos variáveis atuais.")
    b = breakdown(inp, price_minor) if price_minor else None
    if b is not None:
        if b.contribution_minor < 0:
            warnings.append("Margem de contribuição NEGATIVA no preço informado.")
        elif b.margin_bps < inp.target_margin_bps:
            warnings.append("Margem abaixo da meta no preço informado.")
    return Quote(b, breakeven, suggested, warnings)
