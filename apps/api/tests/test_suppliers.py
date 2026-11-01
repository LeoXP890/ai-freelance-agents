import datetime as dt
import uuid

from aicommerce.db import session_scope
from aicommerce.models import SupplierOffer


def mk_product(client, h):
    r = client.post(
        "/v1/products",
        headers=h,
        json={
            "sku_base": f"P-{uuid.uuid4().hex[:6]}",
            "title": "Garrafa teste",
            "category": "Casa",
            "ncm": "96170010",
            "images": ["https://exemplo.invalid/img.jpg"],
            "variants": [
                {
                    "sku": f"V-{uuid.uuid4().hex[:6]}",
                    "weight_g": 380,
                    "length_mm": 75,
                    "width_mm": 75,
                    "height_mm": 260,
                    "gtin": "7890000000017",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def mk_supplier(client, h, origin="NATIONAL"):
    r = client.post(
        "/v1/suppliers",
        headers=h,
        json={
            "name": f"Fornecedor {uuid.uuid4().hex[:4]}",
            "legal_id": "00.000.000/0001-00",
            "origin": origin,
            "country": "BR" if origin == "NATIONAL" else "CN",
            "sla_dispatch_days": 2,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def make_ready(client, op, owner, sup, variant_id, cost=3290):
    """Documentos verificados, amostra aprovada, oferta com estoque → checklist completo."""
    sid = sup["id"]
    for t in ("CNPJ_CARD" if sup["origin"] == "NATIONAL" else "FOREIGN_REGISTRATION", "CONTRACT", "INVOICE_SAMPLE"):
        d = client.post(
            f"/v1/suppliers/{sid}/documents", headers=op, json={"doc_type": t, "reference": f"s3://docs/{t}"}
        )
        client.post(f"/v1/suppliers/{sid}/documents/{d.json()['id']}/verify", headers=owner)
    s = client.get(f"/v1/suppliers/{sid}", headers=op).json()
    client.post(
        f"/v1/suppliers/{sid}/sample",
        headers=owner,
        json={"sample_status": "APPROVED", "expected_version": s["version"]},
    )
    offer = client.post(
        f"/v1/suppliers/{sid}/offers",
        headers=op,
        json={
            "variant_id": variant_id,
            "cost_minor": cost,
            "lead_time_days": 2,
            "stock_qty": 50,
            "source_ref": "cotacao:teste",
        },
    ).json()
    return offer


def approve_supplier(client, owner, sid):
    s = client.get(f"/v1/suppliers/{sid}", headers=owner).json()
    s = client.post(
        f"/v1/suppliers/{sid}/transition",
        headers=owner,
        json={"to": "EVALUATING", "expected_version": s["version"], "reason": "iniciar avaliação"},
    ).json()
    r = client.post(
        f"/v1/suppliers/{sid}/transition",
        headers=owner,
        json={"to": "APPROVED", "expected_version": s["version"], "reason": "checklist completo"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_new_supplier_has_incomplete_checklist_and_high_risk(client, auth):
    s = mk_supplier(client, auth("operator"))
    a = s["assessment"]
    assert s["status"] == "PROSPECT" and a["ready_for_approval"] is False and a["level"] == "ALTO"
    assert {i["key"] for i in a["checklist"]} == {
        "identificacao",
        "contrato",
        "sla",
        "estoque",
        "documentacao",
        "amostra",
    }
    assert "SUP-01" in s["note"]


def test_full_dossier_allows_approval_by_owner_only(client, auth):
    op, owner = auth("operator"), auth("owner")
    prod = mk_product(client, op)
    sup = mk_supplier(client, op)
    make_ready(client, op, owner, sup, prod["variants"][0]["id"])
    s = client.get(f"/v1/suppliers/{sup['id']}", headers=op).json()
    assert s["assessment"]["ready_for_approval"] is True and s["assessment"]["level"] == "BAIXO"
    s = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=op,
        json={"to": "EVALUATING", "expected_version": s["version"], "reason": "avaliar"},
    ).json()
    r = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=op,
        json={"to": "APPROVED", "expected_version": s["version"], "reason": "x" * 5},
    )
    assert r.status_code == 403  # operador sem MFA/sem suppliers:approve
    r = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=owner,
        json={"to": "APPROVED", "expected_version": s["version"], "reason": "aprovado"},
    )
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"


def test_cannot_approve_with_incomplete_checklist(client, auth):
    owner = auth("owner")
    sup = mk_supplier(client, owner)
    s = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=owner,
        json={"to": "EVALUATING", "expected_version": sup["version"], "reason": "avaliar"},
    ).json()
    r = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=owner,
        json={"to": "APPROVED", "expected_version": s["version"], "reason": "tentar"},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CHECKLIST_INCOMPLETE"


def test_version_conflict_and_invalid_transition(client, auth):
    owner = auth("owner")
    sup = mk_supplier(client, owner)
    r = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=owner,
        json={"to": "EVALUATING", "expected_version": sup["version"] + 5, "reason": "x" * 3},
    )
    assert r.json()["detail"]["code"] == "VERSION_CONFLICT"
    r = client.post(
        f"/v1/suppliers/{sup['id']}/transition",
        headers=owner,
        json={"to": "SUSPENDED", "expected_version": sup["version"], "reason": "x" * 3},
    )
    assert r.status_code == 409 and "não permitida" in r.json()["detail"]["message"]


def test_document_verification_and_sample_approval_need_permission(client, auth):
    op = auth("operator")
    sup = mk_supplier(client, op)
    d = client.post(
        f"/v1/suppliers/{sup['id']}/documents", headers=op, json={"doc_type": "CONTRACT", "reference": "s3://x"}
    ).json()
    assert client.post(f"/v1/suppliers/{sup['id']}/documents/{d['id']}/verify", headers=op).status_code == 403
    r = client.post(
        f"/v1/suppliers/{sup['id']}/sample",
        headers=op,
        json={"sample_status": "APPROVED", "expected_version": sup["version"]},
    )
    assert r.status_code == 403
    assert (
        client.post(
            f"/v1/suppliers/{sup['id']}/documents", headers=op, json={"doc_type": "SELFIE", "reference": "s3://x"}
        ).status_code
        == 422
    )


def test_stale_stock_lowers_checklist(client, auth):
    op, owner = auth("operator"), auth("owner")
    prod = mk_product(client, op)
    sup = mk_supplier(client, op)
    offer = make_ready(client, op, owner, sup, prod["variants"][0]["id"])
    with session_scope() as db:
        db.get(SupplierOffer, uuid.UUID(offer["id"])).stock_observed_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
    a = client.get(f"/v1/suppliers/{sup['id']}", headers=op).json()["assessment"]
    assert a["ready_for_approval"] is False and a["stale_offers"] == 1
    client.post(f"/v1/offers/{offer['id']}/stock", headers=op, json={"stock_qty": 30})
    a = client.get(f"/v1/suppliers/{sup['id']}", headers=op).json()["assessment"]
    assert a["ready_for_approval"] is True


def test_catalog_permissions_and_duplicate_sku(client, auth):
    op = auth("operator")
    body = {
        "sku_base": f"DUP-{uuid.uuid4().hex[:5]}",
        "title": "Prod",
        "category": "Xy",
        "variants": [
            {"sku": f"DUPV-{uuid.uuid4().hex[:5]}", "weight_g": 1, "length_mm": 1, "width_mm": 1, "height_mm": 1}
        ],
    }
    assert client.post("/v1/products", headers=auth("viewer"), json=body).status_code == 403
    assert client.post("/v1/products", headers=op, json=body).status_code == 201
    r = client.post("/v1/products", headers=op, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE_SKU"
    assert client.get("/v1/products", headers=auth("viewer")).status_code == 200
