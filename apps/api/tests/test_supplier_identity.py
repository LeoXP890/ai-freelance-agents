"""Identificação do fornecedor real: CNPJ/CPF com dígito verificador inválido não passa no checklist SUP-01.
Números de teste são gerados aqui (não são documentos de empresas ou pessoas reais)."""

from __future__ import annotations

import datetime as dt
import uuid

from aicommerce.commerce.suppliers import _check_digit, assess, valid_brazilian_id
from aicommerce.models import Supplier, SupplierDocument


def make_cnpj(base12: str) -> str:
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _check_digit(base12, w1)
    return base12 + str(d1) + str(_check_digit(base12 + str(d1), [6, *w1]))


def make_cpf(base9: str) -> str:
    d1 = _check_digit(base9, list(range(10, 1, -1)))
    return base9 + str(d1) + str(_check_digit(base9 + str(d1), list(range(11, 1, -1))))


def test_check_digits():
    cnpj, cpf = make_cnpj("123456780001"), make_cpf("123456789")
    assert valid_brazilian_id(cnpj) and valid_brazilian_id(
        f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    )
    assert valid_brazilian_id(cpf)
    bad = cnpj[:-1] + str((int(cnpj[-1]) + 1) % 10)
    for v in (bad, "00.000.000/0001-00", "11111111111111", "123", "", None, "00000000000"):
        assert not valid_brazilian_id(v), v


def supplier(legal_id, simulated):
    return Supplier(
        id=uuid.uuid4(),
        name="F",
        legal_id=legal_id,
        origin="NATIONAL",
        country="BR",
        status="PROSPECT",
        sla_dispatch_days=2,
        notes="",
        is_simulated=simulated,
        version=1,
    )


def card(s):
    now = dt.datetime.now(dt.UTC)
    return [
        SupplierDocument(
            id=uuid.uuid4(),
            supplier_id=s.id,
            doc_type="CNPJ_CARD",
            reference="x",
            sha256="a" * 64,
            verified_by="owner",
            verified_at=now,
            expires_at=now + dt.timedelta(days=30),
        )
    ]


def ident(a):
    return next(i for i in a.checklist if i.key == "identificacao")


def test_real_supplier_with_invalid_cnpj_fails_identification_even_with_document():
    s = supplier("12.345.678/0001-00", simulated=False)
    item = ident(assess(s, card(s), []))
    assert not item.ok and "dígito verificador" in item.detail


def test_real_supplier_with_valid_cnpj_and_verified_card_passes_identification():
    s = supplier(make_cnpj("123456780001"), simulated=False)
    assert ident(assess(s, card(s), [])).ok


def test_simulated_supplier_keeps_fictitious_id():
    s = supplier("00.000.000/0001-00 (fictício)", simulated=True)
    assert ident(assess(s, card(s), [])).ok
