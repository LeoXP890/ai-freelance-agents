# ruff: noqa: F811  (fixture pytest importada: shop)
"""COM-012: estorno e chargeback nas linhas de repasse; conciliação por pedido."""

import uuid

from tests.test_orders import order, shop, to_delivered  # noqa: F401  (fixture shop)


def statement(lines):
    return {
        "channel": "WOOCOMMERCE",
        "external_id": f"EXT-{uuid.uuid4().hex[:8]}",
        "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-10-07T00:00:00Z",
        "lines": lines,
    }


def rec(client, fin):
    return client.get("/v1/finance/reconciliation?channel=WOOCOMMERCE", headers=fin).json()


def row(rows, ext):
    return next((r for r in rows if r["order_external_id"] == ext), None)


def test_chargeback_opens_dispute_alert_and_reversal_restores(client, shop, auth):
    fin = auth("finance")
    o = order(client, shop)
    client.post("/v1/sim/settlements", headers=shop["op"], json={"channel": "WOOCOMMERCE"})
    assert row(rec(client, fin)["matched"], o["external_id"])
    cb = statement(
        [
            {
                "order_external_id": o["external_id"],
                "gross_minor": o["total_minor"],
                "fee_minor": 1500,
                "kind": "CHARGEBACK",
            }
        ]
    )
    assert client.post("/v1/finance/settlements/import", headers=fin, json=cb).status_code == 201
    assert client.get(f"/v1/orders/{o['id']}", headers=shop["op"]).json()["payment_status"] == "CHARGEBACK"
    r = rec(client, fin)
    d = row(r["disputes"], o["external_id"])
    assert d and d["at_risk_minor"] == o["total_minor"] + 1500
    assert not row(r["divergent"], o["external_id"]) and not row(r["matched"], o["external_id"])
    alerts = client.get("/v1/alerts", headers=fin).json()["items"]
    assert any(a["kind"] == "CHARGEBACK_OPEN" and a["ref"] == f"order:{o['id']}" for a in alerts)

    rev = statement(
        [
            {
                "order_external_id": o["external_id"],
                "gross_minor": o["total_minor"],
                "fee_minor": 0,
                "kind": "CHARGEBACK_REVERSAL",
            }
        ]
    )
    client.post("/v1/finance/settlements/import", headers=fin, json=rev)
    assert client.get(f"/v1/orders/{o['id']}", headers=shop["op"]).json()["payment_status"] == "CAPTURED"
    r = rec(client, fin)
    div = row(r["divergent"], o["external_id"])  # disputa ganha, mas a tarifa do chargeback ficou com o canal
    assert div and div["diff_minor"] == -1500 and div["kinds"] == ["CHARGEBACK", "CHARGEBACK_REVERSAL", "SALE"]
    assert not row(r["disputes"], o["external_id"])


def test_refund_line_reconciles_against_refunded_order(client, shop, auth):
    fin = auth("finance")
    o = to_delivered(client, shop, order(client, shop))
    client.post("/v1/sim/settlements", headers=shop["op"], json={"channel": "WOOCOMMERCE"})
    r = client.post(f"/v1/orders/{o['id']}/returns", headers=shop["op"], json={"reason": "ARREPENDIMENTO"}).json()
    from tests.test_approvals import decide
    from tests.test_orders import drain

    assert decide(client, shop["owner"], r["proposal"]).status_code == 200
    drain()
    assert client.get(f"/v1/orders/{o['id']}", headers=shop["op"]).json()["payment_status"] == "REFUNDED"
    pending = row(rec(client, fin)["divergent"], o["external_id"])
    assert pending and "ainda não debitado" in pending["note"]  # reembolso feito, canal ainda não debitou
    ref = statement(
        [{"order_external_id": o["external_id"], "gross_minor": r["refund_minor"], "fee_minor": 0, "kind": "REFUND"}]
    )
    client.post("/v1/finance/settlements/import", headers=fin, json=ref)
    m = row(rec(client, fin)["matched"], o["external_id"])
    assert m and m["kinds"] == ["REFUND", "SALE"]


def test_statement_validation(client, auth):
    fin = auth("finance")
    neg = statement([{"order_external_id": "X", "gross_minor": -100, "fee_minor": 0, "kind": "REFUND"}])
    assert client.post("/v1/finance/settlements/import", headers=fin, json=neg).status_code == 422
    bad = statement([{"order_external_id": "X", "gross_minor": 100, "fee_minor": 0, "kind": "ESTORNO"}])
    assert client.post("/v1/finance/settlements/import", headers=fin, json=bad).status_code == 422
    mixed = statement(
        [
            {"order_external_id": "Y1", "gross_minor": 10_000, "fee_minor": 1_000, "kind": "SALE"},
            {"order_external_id": "Y1", "gross_minor": 10_000, "fee_minor": -500, "kind": "REFUND"},
        ]
    )
    body = client.post("/v1/finance/settlements/import", headers=fin, json=mixed).json()
    assert body["net_minor"] == (10_000 - 1_000) + (-10_000 + 500)  # comissão parcialmente devolvida
