"""COM-001: edição com controle de versão, histórico imutável, arquivamento e mídia."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aicommerce.db import session_scope
from tests.test_listings import ctx, prepare  # noqa: F401  (fixture ctx)
from tests.test_suppliers import mk_product

IMG = "https://cdn.exemplo.com.br/p/garrafa-1.webp"


def test_update_versions_history_and_optimistic_conflict(client, auth):
    op = auth("operator")
    prod = mk_product(client, op)
    assert prod["version"] == 1
    r = client.put(
        f"/v1/products/{prod['id']}",
        headers=op,
        json={"expected_version": 1, "title": "Garrafa térmica 750 ml inox", "ncm": "39241000"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 2 and sorted(body["changed"]) == ["ncm", "title"]
    # outra aba ainda na v1: conflito, nada muda
    stale = client.put(f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 1, "title": "Outra coisa"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "VERSION_CONFLICT"
    same = client.put(
        f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 2, "title": "Garrafa térmica 750 ml inox"}
    )
    assert same.json()["version"] == 2 and same.json()["changed"] == []  # sem mudança, sem nova versão
    hist = client.get(f"/v1/products/{prod['id']}/versions", headers=op).json()["items"]
    assert [h["version"] for h in hist] == [2, 1]
    assert hist[0]["snapshot"]["title"] == "Garrafa térmica 750 ml inox" and hist[1]["changed_fields"] == ["created"]
    assert (
        client.put(
            f"/v1/products/{prod['id']}", headers=auth("viewer"), json={"expected_version": 2, "title": "x" * 5}
        ).status_code
        == 403
    )


def test_versions_are_append_only():
    with session_scope() as db, pytest.raises(DBAPIError):
        db.execute(text("UPDATE product_versions SET changed_by = 'adulterado' WHERE version = 1"))


def test_media_validation_order_and_images_sync(client, auth):
    op = auth("operator")
    prod = mk_product(client, op)
    media = [
        {"url": IMG, "alt": "Garrafa térmica azul, vista frontal"},
        {"url": "https://cdn.exemplo.com.br/p/garrafa-2.jpg", "alt": "Tampa aberta"},
    ]
    r = client.put(f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 1, "media": media})
    assert r.status_code == 200 and r.json()["images"] == [m["url"] for m in media]
    reordered = list(reversed(media))
    r = client.put(f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 2, "media": reordered})
    assert r.json()["images"][0].endswith("garrafa-2.jpg") and r.json()["changed"] == ["media"]
    for bad in (
        [{"url": "http://cdn.exemplo.com.br/a.jpg", "alt": "sem https"}],
        [{"url": "https://cdn.exemplo.com.br/a.gif", "alt": "formato"}],
        [{"url": IMG, "alt": "dup"}, {"url": IMG, "alt": "dup2"}],
        [{"url": f"https://cdn.exemplo.com.br/{i}.png", "alt": "muitas"} for i in range(11)],
    ):
        assert (
            client.put(f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 3, "media": bad}).status_code
            == 422
        )
    sem_alt = client.put(
        f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 3, "media": [{"url": IMG, "alt": ""}]}
    )
    assert sem_alt.status_code == 422


def test_edit_marks_prepared_listing_and_archive_rules(client, ctx):  # noqa: F811
    op = ctx["op"]
    x = prepare(client, ctx)
    pid = ctx["product"]["id"]
    v = client.get(f"/v1/products/{pid}", headers=op).json()["version"]
    r = client.put(f"/v1/products/{pid}", headers=op, json={"expected_version": v, "description": "Nova descrição"})
    assert r.json()["listings_marked"] >= 1
    codes = {i["code"] for i in client.get(f"/v1/listings/{x['id']}", headers=op).json()["compliance"]}
    assert "PRODUCT_CHANGED" in codes
    # Marcado como obsoleto: não permitir aprovar payload anterior à edição.
    stale = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "COMPLIANCE_BLOCKED"
    # Preparar nova versão libera a proposta (e impede arquivar enquanto aguarda decisão).
    refreshed = prepare(client, ctx)
    assert refreshed["version"] == 2 and "PRODUCT_CHANGED" not in {i["code"] for i in refreshed["compliance"]}
    proposed = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op)
    assert proposed.status_code == 201
    blocked = client.post(f"/v1/products/{pid}/archive", headers=op, json={"reason": "descontinuado"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "BLOCKED"


def test_archive_blocks_new_listing_and_editing_then_reactivate(client, auth):
    op = auth("operator")
    prod = mk_product(client, op)
    r = client.post(f"/v1/products/{prod['id']}/archive", headers=op, json={"reason": "fora de linha"})
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED" and r.json()["version"] == 2
    assert (
        client.put(
            f"/v1/products/{prod['id']}", headers=op, json={"expected_version": 2, "title": "Não pode"}
        ).status_code
        == 409
    )
    back = client.post(f"/v1/products/{prod['id']}/reactivate", headers=op, json={"reason": "voltou"})
    assert back.json()["status"] == "ACTIVE" and back.json()["version"] == 3
    hist = client.get(f"/v1/products/{prod['id']}/versions", headers=op).json()["items"]
    assert [h["snapshot"]["status"] for h in hist[:2]] == ["ACTIVE", "ARCHIVED"]
    assert client.get(f"/v1/products/{uuid.uuid4()}/versions", headers=op).json()["items"] == []


def test_variant_edit_versions_product_and_guards_live_listing(client, ctx):  # noqa: F811
    op = ctx["op"]
    pid, vid = ctx["product"]["id"], ctx["variant_id"]
    v = client.get(f"/v1/products/{pid}", headers=op).json()["version"]
    r = client.put(
        f"/v1/variants/{vid}", headers=op, json={"expected_version": v, "weight_g": 777, "gtin": "7891234567895"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == v + 1 and sorted(r.json()["changed"])[0].endswith(":gtin")
    variant = next(x for x in r.json()["variants"] if x["id"] == vid)
    assert variant["weight_g"] == 777 and variant["gtin"] == "7891234567895"
    hist = client.get(f"/v1/products/{pid}/versions", headers=op).json()["items"]
    snap_v = next(x for x in hist[0]["snapshot"]["variants"] if x["gtin"] == "7891234567895")
    assert snap_v["weight_g"] == 777
    assert (
        client.put(f"/v1/variants/{vid}", headers=op, json={"expected_version": v, "weight_g": 1}).status_code == 409
    )  # versão velha
    x = prepare(client, ctx)
    client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op)
    off = client.put(f"/v1/variants/{vid}", headers=op, json={"expected_version": v + 1, "status": "INACTIVE"})
    assert off.status_code == 409 and "pause" in off.json()["detail"]["message"]
