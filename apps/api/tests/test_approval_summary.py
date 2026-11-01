"""Lista de aprovações identifica cada proposta (finalização do desenvolvimento, §5): objeto afetado, canal, motivo e
detalhe vêm da própria proposta ou do registro afetado — o que não existe fica vazio (nada inventado)."""

from __future__ import annotations

from tests.test_orders import order, shop  # noqa: F401 — fixture com anúncio publicado pelo fluxo real
from tests.test_proposals import proposal


def items(client, h):
    return {p["id"]: p for p in client.get("/v1/approvals", headers=h).json()["items"]}


def test_listing_proposal_shows_product_channel_and_sku(client, shop):  # noqa: F811
    x = shop["listing"]
    r = client.post(f"/v1/listings/{x['id']}/propose-pause", headers=shop["op"], json={"reason": "estoque acabando"})
    assert r.status_code in (200, 201), r.text
    s = items(client, shop["owner"])[r.json()["id"]]["summary"]
    assert s["object"] == "Garrafa teste" and s["channel"] == x["channel"] and s["detail"] == shop["sku"]
    assert s["reason"] == "estoque acabando"


def test_cancellation_refund_shows_order_and_reason(client, shop):  # noqa: F811
    o = order(client, shop, paid=True)
    client.post(f"/v1/orders/{o['id']}/cancel", headers=shop["op"], json={"reason": "cliente desistiu"})
    s = next(p["summary"] for p in items(client, shop["owner"]).values() if p["subject_id"] == o["id"])
    assert s["object"].startswith("Pedido ") and s["reason"] == "pedido cancelado após pagamento"


def test_missing_data_is_not_invented(client, auth):
    h = auth("operator")
    r = client.post("/v1/proposals", json=proposal(payload={"price_minor": 9990}, go_live=None), headers=h)
    assert r.status_code == 201, r.text
    s = items(client, auth("owner"))[r.json()["id"]]["summary"]
    assert s == {"object": None, "detail": None, "channel": None, "reason": None}
