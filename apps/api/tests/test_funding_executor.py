"""Executor de SUPPLIER_PURCHASE exige caixa (ADR 0008) quando as fontes estão configuradas — fluxo real de pedido.

Pedido simulado → fiscal → proposta de compra → aprovação do proprietário → executor → fornecedor simulado.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.finance import funding
from aicommerce.governance import executor
from aicommerce.models import FundingAllocation, SimProviderEffect
from aicommerce.providers.mock import MockSupplierProvider
from tests.test_approvals import decide
from tests.test_orders import drain, get, order, shop  # noqa: F401  (fixture)

FUTURE = dt.timedelta(minutes=10)


def uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def cash(monkeypatch):
    """Fontes simuladas novas, configuradas e com leitura fresca; o executor passa a exigir caixa."""

    def _make(op_available, res_available=None, **reserve_limits):
        with session_scope() as s:
            op = funding.ensure_source(s, f"op-{uid()}", "OPERATING", "Saldo liberado (SIMULADO)")
            funding.configure_source(s, op.id, floor_minor=0, approved_by="owner")
            funding.record_snapshot(
                s,
                op.id,
                available_minor=op_available,
                currency="BRL",
                observed_at=dt.datetime.now(dt.UTC),
                origin="SIMULATED",
                idempotency_key=f"snap-{uid()}",
                actor="test",
            )
            codes = {"op": op.code, "op_id": op.id, "res": "", "res_id": None}
            if res_available is not None:
                res = funding.ensure_source(s, f"rs-{uid()}", "RESERVE", "Fundo de reserva (SIMULADO)")
                limits = {
                    "per_order_limit_minor": 10**9,
                    "period_limit_minor": 10**9,
                    "period_days": 30,
                    "max_exposure_minor": 10**9,
                    **reserve_limits,
                }
                funding.configure_source(s, res.id, floor_minor=0, approved_by="owner", **limits)
                funding.record_snapshot(
                    s,
                    res.id,
                    available_minor=res_available,
                    currency="BRL",
                    observed_at=dt.datetime.now(dt.UTC),
                    origin="SIMULATED",
                    idempotency_key=f"snap-{uid()}",
                    actor="test",
                )
                codes.update(res=res.code, res_id=res.id)
        monkeypatch.setattr(get_settings(), "funding_operating_source", codes["op"])
        monkeypatch.setattr(get_settings(), "funding_reserve_source", codes["res"])
        return codes

    return _make


def fault(monkeypatch, name):
    original = MockSupplierProvider.execute

    def faulty(self, cmd):
        cmd.payload["_simulate"] = name
        return original(self, cmd)

    monkeypatch.setattr(MockSupplierProvider, "execute", faulty)


def request_purchase(client, shop, qty=1):  # noqa: F811
    o = order(client, shop, qty=qty)
    drain()  # fiscal simulado
    props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=shop["op"]).json()["proposals"]
    assert decide(client, shop["owner"], props[0]).status_code == 200
    return o, props[0]


def allocation(prop_id):
    with session_scope() as s:
        rows = (
            s.execute(select(FundingAllocation).where(FundingAllocation.proposal_id == uuid.UUID(prop_id)))
            .scalars()
            .all()
        )
        return [(a.status, a.block_reason, funding.legs(s, a.id), a.amount_minor) for a in rows]


def supplier_effects(prop_id):
    with session_scope() as s:
        return len(
            s.execute(
                select(SimProviderEffect).where(
                    SimProviderEffect.provider == "mock-supplier",
                    SimProviderEffect.idempotency_key.like(f"%{prop_id}%"),
                )
            )
            .scalars()
            .all()
        )


def test_disabled_by_default_keeps_budget_only_flow(client, shop):  # noqa: F811
    assert get_settings().funding_operating_source == ""
    o, prop = request_purchase(client, shop)
    drain()
    assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED" and allocation(prop["id"]) == []


def test_operating_balance_pays_supplier(client, shop, cash):  # noqa: F811
    cost = shop["offer"]["cost_minor"]
    c = cash(op_available=10 * cost)
    o, prop = request_purchase(client, shop, qty=2)
    drain()
    assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED"
    assert allocation(prop["id"]) == [("PAID", None, {"OPERATING": 2 * cost}, 2 * cost)]
    with session_scope() as s:
        assert funding.source_view(s, c["op_id"]).committed_minor == 2 * cost  # pago após a leitura usada


def test_reserve_completes_partial_balance(client, shop, cash):  # noqa: F811
    cost = shop["offer"]["cost_minor"]
    c = cash(op_available=cost, res_available=10 * cost)
    o, prop = request_purchase(client, shop, qty=3)
    drain()
    assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED"
    assert allocation(prop["id"])[0][:3] == ("PAID", None, {"OPERATING": cost, "RESERVE": 2 * cost})
    with session_scope() as s:
        assert funding.source_view(s, c["res_id"]).exposure_minor == 2 * cost


def test_insufficient_cash_blocks_before_calling_supplier(client, shop, cash):  # noqa: F811
    cost = shop["offer"]["cost_minor"]
    cash(op_available=cost, res_available=cost)
    o, prop = request_purchase(client, shop, qty=3)
    drain()
    after = get(client, shop, o["id"])
    assert after["status"] == "INCIDENT" and after["fulfillments"][0]["status"] == "FAILED"
    assert allocation(prop["id"]) == [("BLOCKED", "RESERVE_INSUFFICIENT", {}, 3 * cost)]
    assert supplier_effects(prop["id"]) == 0  # fornecedor nunca foi chamado
    job = client.get(f"/v1/approvals/{prop['id']}", headers=shop["owner"]).json()["job"]
    assert job["status"] == "FAILED" and "RESERVE_INSUFFICIENT" in job["last_error"]


def test_timeout_after_debit_stays_unknown_then_reconciles_once(client, shop, cash, monkeypatch):  # noqa: F811
    cost = shop["offer"]["cost_minor"]
    c = cash(op_available=10 * cost)
    fault(monkeypatch, "timeout_after")
    o, prop = request_purchase(client, shop)
    assert executor.execute_next()
    assert allocation(prop["id"])[0][0] == "PAYMENT_UNKNOWN"
    with session_scope() as s:
        assert funding.source_view(s, c["op_id"]).committed_minor == cost  # continua comprometido
    assert executor.reconcile_next(dt.datetime.now(dt.UTC) + FUTURE)
    assert allocation(prop["id"])[0][0] == "PAID"
    assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED"


def test_timeout_before_debit_retries_with_new_allocation(client, shop, cash, monkeypatch):  # noqa: F811
    cost = shop["offer"]["cost_minor"]
    cash(op_available=10 * cost)
    fault(monkeypatch, "timeout_before")
    o, prop = request_purchase(client, shop)
    executor.execute_next()
    executor.reconcile_next(dt.datetime.now(dt.UTC) + FUTURE)  # provedor confirma ausência → retry seguro
    while executor.execute_next(dt.datetime.now(dt.UTC) + FUTURE) or executor.reconcile_next(
        dt.datetime.now(dt.UTC) + FUTURE
    ):
        pass
    statuses = sorted(a[0] for a in allocation(prop["id"]))
    assert statuses == ["FAILED", "PAID"]  # uma tentativa sem débito, uma paga; nunca duas pagas
    assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED"


def test_emergency_stop_still_funds_contracted_orders(client, shop, cash):  # noqa: F811
    cost = shop["offer"]["cost_minor"]
    cash(op_available=10 * cost)
    o = order(client, shop)
    drain()
    client.post("/v1/emergency-stop", json={"reason": "teste caixa"}, headers=shop["owner"])
    try:
        props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=shop["op"]).json()["proposals"]
        assert decide(client, shop["owner"], props[0]).status_code == 200
        drain()
        assert get(client, shop, o["id"])["status"] == "SUPPLIER_ACCEPTED"
        assert allocation(props[0]["id"])[0][0] == "PAID"
    finally:
        client.post("/v1/emergency-stop/release", json={"reason": "fim"}, headers=shop["owner"])


def test_second_purchase_for_same_order_and_supplier_is_refused(client, shop, cash):  # noqa: F811
    """Mesmo que surja outra proposta (com envio próprio) para o mesmo pedido e fornecedor, o caixa não paga de novo."""
    from aicommerce.models import ApprovalProposal, Fulfillment

    cost = shop["offer"]["cost_minor"]
    c = cash(op_available=10 * cost)
    _, prop = request_purchase(client, shop)
    drain()
    with session_scope() as s:
        p = s.get(ApprovalProposal, uuid.UUID(prop["id"]))
        skip = ("id", "idempotency_key", "created_at", "updated_at")
        clone = ApprovalProposal(
            **{col.name: getattr(p, col.name) for col in ApprovalProposal.__table__.columns if col.name not in skip},
            idempotency_key=f"clone-{uid()}",
        )
        s.add(clone)
        s.flush()
        first = s.execute(select(Fulfillment).where(Fulfillment.proposal_id == p.id)).scalar_one()
        s.add(
            Fulfillment(
                order_id=first.order_id,
                supplier_id=first.supplier_id,
                status="REQUESTED",
                cost_minor=first.cost_minor,
                promised_by=first.promised_by,
                proposal_id=clone.id,
            )
        )
        s.flush()
        reason = funding.open_for_proposal(s, clone, deadline=dt.datetime.now(dt.UTC) + FUTURE)
        committed = funding.source_view(s, c["op_id"]).committed_minor
    assert reason == "obrigação deste pedido com este fornecedor já financiada por outra proposta"
    assert committed == cost and allocation(prop["id"])[0][0] == "PAID"
