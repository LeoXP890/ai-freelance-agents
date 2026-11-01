import pytest

from aicommerce.commerce.pricing import PricingInput, breakdown, pct_of, quote


def inp(**over):
    base = dict(
        cost_minor=3290,
        shipping_minor=1500,
        channel_fixed_fee_minor=600,
        ads_cpa_minor=800,
        commission_bps=1400,
        payment_fee_bps=0,
        tax_bps=600,
        returns_bps=200,
        target_margin_bps=1500,
        fee_schedule_verified=False,
        tax_approved=False,
    )
    base.update(over)
    return PricingInput(**base)


def test_breakdown_is_integer_and_conservative():
    b = breakdown(inp(), 9990)
    assert b.commission_minor == pct_of(9990, 1400) == 1399  # 1398,6 arredonda para cima
    assert b.tax_minor == 600 and b.returns_minor == 200
    assert b.contribution_minor == 9990 - 1399 - 0 - 600 - 200 - (3290 + 1500 + 600 + 800)
    assert b.margin_bps == b.contribution_minor * 10_000 // 9990


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"commission_bps": 0, "payment_fee_bps": 399, "channel_fixed_fee_minor": 0},
        {"cost_minor": 1, "shipping_minor": 0, "ads_cpa_minor": 0},
        {"target_margin_bps": 0},
        {"returns_bps": 1234, "tax_bps": 777},
    ],
)
def test_breakeven_and_suggested_are_minimal(over):
    i = inp(**over)
    q = quote(i)
    be, sg = q.breakeven_minor, q.suggested_minor
    assert breakdown(i, be).contribution_minor >= 0
    assert be == 1 or breakdown(i, be - 1).contribution_minor < 0
    b = breakdown(i, sg)
    assert b.contribution_minor >= pct_of(sg, i.target_margin_bps)
    assert breakdown(i, sg - 1).contribution_minor < pct_of(sg - 1, i.target_margin_bps)
    assert sg >= be


def test_impossible_when_variable_costs_reach_100_percent():
    q = quote(inp(commission_bps=6000, tax_bps=4000))
    assert q.breakeven_minor is None and any("100%" in w for w in q.warnings)
    q2 = quote(inp(commission_bps=5000, tax_bps=4000, target_margin_bps=2000))
    assert q2.breakeven_minor is not None and q2.suggested_minor is None


def test_warnings_flag_unverified_params_and_negative_margin():
    q = quote(inp(), price_minor=5000)
    joined = " ".join(q.warnings)
    assert "FICTÍCIAS" in joined and "contador" in joined and "NEGATIVA" in joined
    ok = quote(inp(fee_schedule_verified=True, tax_approved=True), price_minor=50_000)
    assert ok.warnings == []


def test_negative_inputs_rejected():
    with pytest.raises(ValueError):
        inp(cost_minor=-1)
    with pytest.raises(ValueError):
        breakdown(inp(), 0)


def test_quote_api_uses_seeded_unverified_params(client, auth):
    r = client.post(
        "/v1/pricing/quote",
        headers=auth("viewer"),
        json={"channel": "MERCADO_LIVRE", "cost_minor": 3290, "shipping_minor": 1500, "price_minor": 9990},
    )
    body = r.json()
    assert r.status_code == 200 and body["params"]["fee_schedule"]["verified"] is False
    assert body["params"]["tax_profile"]["approved_by_accountant"] is False and body["suggested_minor"] > 0
