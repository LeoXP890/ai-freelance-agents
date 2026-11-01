import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aicommerce.db import session_scope
from aicommerce.finance import ledger


def key():
    return f"k-{uuid.uuid4().hex}"


def view(acc):
    with session_scope() as s:
        return ledger.budget_view(s, acc)


def reserve(acc, amount, k=None, currency="BRL"):
    with session_scope() as s:
        r, created = ledger.reserve(
            s, acc, amount_minor=amount, currency=currency, idempotency_key=k or key(), purpose="teste", actor="test"
        )
        return r.id, created


def spend(acc, amount, resv=None, k=None, ref="ext-1"):
    with session_scope() as s:
        return ledger.record_spend(
            s,
            acc,
            amount_minor=amount,
            currency="BRL",
            external_ref=ref,
            idempotency_key=k or key(),
            actor="test",
            reservation_id=resv,
        )


def test_available_formula(budget_account):
    acc = budget_account(cap=100_000, hold=10_000)
    assert view(acc).available_minor == 90_000
    reserve(acc, 30_000)
    spend(acc, 5_000)  # gasto sem reserva
    v = view(acc)
    assert (v.reconciled_spend_minor, v.outstanding_reservations_minor, v.available_minor) == (5_000, 30_000, 55_000)
    assert v.simulated is True


def test_insufficient_budget_rejected(budget_account):
    acc = budget_account(cap=1_000, hold=100)
    with pytest.raises(ledger.InsufficientBudget):
        reserve(acc, 901)
    reserve(acc, 900)
    with pytest.raises(ledger.InsufficientBudget):
        reserve(acc, 1)


def test_reserve_is_idempotent_and_conflicts_on_different_payload(budget_account):
    acc = budget_account()
    k = key()
    a, created_a = reserve(acc, 1_000, k)
    b, created_b = reserve(acc, 1_000, k)
    assert a == b and created_a and not created_b
    assert view(acc).outstanding_reservations_minor == 1_000
    with pytest.raises(ledger.IdempotencyConflict):
        reserve(acc, 2_000, k)


def test_spend_consumes_reservation_without_double_subtraction(budget_account):
    acc = budget_account(cap=10_000, hold=0)
    rid, _ = reserve(acc, 4_000)
    assert view(acc).available_minor == 6_000
    spend(acc, 1_500, rid)
    v = view(acc)
    assert (v.reconciled_spend_minor, v.outstanding_reservations_minor, v.available_minor) == (1_500, 2_500, 6_000)
    spend(acc, 2_500, rid)
    v = view(acc)
    assert (v.outstanding_reservations_minor, v.available_minor) == (0, 6_000)
    with session_scope() as s:
        assert s.get(ledger.BudgetReservation, rid).status == "CONSUMED"


def test_external_overspend_is_recorded_and_flagged(budget_account):
    acc = budget_account(cap=1_000, hold=0)
    rid, _ = reserve(acc, 1_000)
    out = spend(acc, 1_300, rid)  # plataforma gastou acima da média diária
    assert out["overspend"] is True and out["budget"]["available_minor"] == -300


def test_spend_replay_is_idempotent(budget_account):
    acc = budget_account()
    k = key()
    first, second = spend(acc, 700, k=k), spend(acc, 700, k=k)
    assert second["replayed"] is True and first["entry_id"] == second["entry_id"]
    assert view(acc).reconciled_spend_minor == 700
    with pytest.raises(ledger.IdempotencyConflict):
        spend(acc, 701, k=k)


def test_release_returns_remaining_and_cannot_repeat(budget_account):
    acc = budget_account(cap=10_000, hold=0)
    rid, _ = reserve(acc, 3_000)
    spend(acc, 1_000, rid)
    with session_scope() as s:
        ledger.release(s, rid, actor="test", reason="campanha encerrada")
    assert view(acc).available_minor == 9_000
    with pytest.raises(ledger.InvalidReservationState), session_scope() as s:
        ledger.release(s, rid, actor="test", reason="de novo")
    with pytest.raises(ledger.InvalidReservationState):
        spend(acc, 10, rid)


def test_currency_mismatch_rejected(budget_account):
    with pytest.raises(ledger.LedgerError, match="moeda"):
        reserve(budget_account(), 100, currency="USD")


def test_ledger_entries_are_immutable_in_database(budget_account):
    acc = budget_account()
    reserve(acc, 100)
    for sql in (
        "UPDATE ledger_entries SET amount_minor = 1",
        "DELETE FROM ledger_entries",
        "TRUNCATE ledger_entries CASCADE",
    ):
        with pytest.raises(DBAPIError, match="append-only"), session_scope() as s:
            s.execute(text(sql))


def test_concurrent_reservations_never_exceed_available(budget_account):
    """Race condition de orçamento (SDD §18): 40 reservas simultâneas de 1.000 com 10.000 disponíveis."""
    acc = budget_account(cap=12_000, hold=2_000)
    barrier = threading.Barrier(40)
    results: list[str] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            reserve(acc, 1_000)
            outcome = "ok"
        except ledger.InsufficientBudget:
            outcome = "insufficient"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 10 and results.count("insufficient") == 30
    v = view(acc)
    assert v.available_minor == 0 and v.outstanding_reservations_minor == 10_000


def test_concurrent_replays_of_same_key_create_single_reservation(budget_account):
    acc = budget_account()
    k = key()
    barrier = threading.Barrier(10)
    ids = []

    def worker():
        barrier.wait()
        ids.append(reserve(acc, 500, k)[0])

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 1 and view(acc).outstanding_reservations_minor == 500


def test_finance_api_roundtrip_and_permissions(client, auth, budget_account):
    acc = str(budget_account(cap=50_000, hold=0))
    fin = auth("finance")
    body = {"account_id": acc, "amount_minor": 20_000, "purpose": "campanha teste", "idempotency_key": key()}
    assert client.post("/v1/finance/reservations", json=body, headers=auth("viewer")).status_code == 403
    r = client.post("/v1/finance/reservations", json=body, headers=fin)
    assert r.status_code == 201 and r.json()["replayed"] is False
    assert client.post("/v1/finance/reservations", json=body, headers=fin).json()["replayed"] is True
    too_much = {**body, "amount_minor": 40_000, "idempotency_key": key()}
    r2 = client.post("/v1/finance/reservations", json=too_much, headers=fin)
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "INSUFFICIENT_BUDGET"
    ov = client.get("/v1/finance/overview", headers=auth("viewer")).json()["accounts"]
    mine = next(a for a in ov if a["account_id"] == acc)
    assert mine["available_minor"] == 30_000 and mine["simulated"] is True
    # finance não define teto; apenas owner (finance:manage)
    assert (
        client.put(
            f"/v1/finance/accounts/{acc}/budget", json={"approved_cap_minor": 1, "safety_hold_minor": 0}, headers=fin
        ).status_code
        == 403
    )
    r3 = client.put(
        f"/v1/finance/accounts/{acc}/budget",
        json={"approved_cap_minor": 60_000, "safety_hold_minor": 0},
        headers=auth("owner"),
    )
    assert r3.status_code == 200 and r3.json()["available_minor"] == 40_000
