"""Pagamento ao fornecedor com saldo liberado + fundo de reserva (ADR 0008), somente DRY_RUN.

Critérios do ADR: saldo suficiente; parcial + reserva; retido ignorado; reserva insuficiente; limites; concorrência;
evento duplicado/fora de ordem; cotação vencida; beneficiário alterado; timeout antes/depois do débito; crash;
cancelamento/chargeback; recebível posterior sem segundo pagamento; moeda divergente.
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from aicommerce.config import RuntimeMode, get_settings
from aicommerce.db import session_scope
from aicommerce.finance import funding
from aicommerce.models import FundingAllocation, FundingEvent, Order, Supplier


def now():
    return dt.datetime.now(dt.UTC)


def uid():
    return uuid.uuid4().hex[:10]


@pytest.fixture
def world():
    """Fontes simuladas novas + pedido pago pronto para compra + fornecedor aprovado (isolamento por dados)."""

    def _make(
        *,
        op_floor=0,
        reserve=True,
        per_order=100_000,
        period=100_000,
        days=30,
        exposure=100_000,
        op=None,
        res=None,
        order_status="READY_TO_FULFILL",
        payment="CAPTURED",
        supplier_status="APPROVED",
    ):
        with session_scope() as s:
            o = funding.ensure_source(s, f"op-{uid()}", "OPERATING", "Saldo liberado (SIMULADO)")
            funding.configure_source(s, o.id, floor_minor=op_floor, approved_by="owner")
            r = None
            if reserve:
                r = funding.ensure_source(s, f"rs-{uid()}", "RESERVE", "Fundo de reserva (SIMULADO)")
                funding.configure_source(
                    s,
                    r.id,
                    floor_minor=0,
                    approved_by="owner",
                    per_order_limit_minor=per_order,
                    period_limit_minor=period,
                    period_days=days,
                    max_exposure_minor=exposure,
                )
            order = Order(
                channel="MERCADO_LIVRE",
                external_id=f"SIM-{uid()}",
                status=order_status,
                payment_status=payment,
                items_total_minor=20_000,
                total_minor=20_000,
                customer_ref="sim",
                placed_at=now(),
                is_simulated=True,
            )
            sup = Supplier(
                name=f"Fornecedor SIM {uid()}",
                origin="NATIONAL",
                country="BR",
                status=supplier_status,
                is_simulated=True,
            )
            s.add_all([order, sup])
            s.flush()
            ids = {"op": o.id, "res": r.id if r else None, "order": order.id, "supplier": sup.id}
        if op is not None:
            snap(ids["op"], op)
        if res is not None and ids["res"]:
            snap(ids["res"], res)
        return ids

    return _make


def snap(source_id, available, retained=0, at=None, key=None):
    with session_scope() as s:
        out, _ = funding.record_snapshot(
            s,
            source_id,
            available_minor=available,
            retained_minor=retained,
            currency="BRL",
            observed_at=at or now(),
            origin="SIMULATED",
            idempotency_key=key or f"snap-{uid()}",
            actor="test",
        )
        return out.id


def allocate(w, amount, *, key=None, currency="BRL", beneficiary="pix:sim-fornecedor", valid=None, reserve=True):
    with session_scope() as s:
        a, created = funding.allocate(
            s,
            obligation_key=key or f"supplier:{w['order']}:{w['supplier']}:v1",
            amount_minor=amount,
            currency=currency,
            quote_ref="quote-v1",
            quote_valid_until=valid or now() + dt.timedelta(hours=2),
            beneficiary_ref=beneficiary,
            order_id=w["order"],
            supplier_id=w["supplier"],
            operating_source_id=w["op"],
            reserve_source_id=w["res"] if reserve else None,
            actor="test",
        )
        return a.id, a.status, a.block_reason, funding.legs(s, a.id), created


def view(source_id):
    with session_scope() as s:
        return funding.source_view(s, source_id)


def status(aid):
    with session_scope() as s:
        return s.get(FundingAllocation, aid).status


def start(aid, beneficiary="pix:sim-fornecedor"):
    with session_scope() as s:
        return funding.start_payment(s, aid, beneficiary_ref=beneficiary, actor="test")


def result(aid, outcome, key=None, ref=None):
    with session_scope() as s:
        a, applied = funding.apply_payment_result(
            s, aid, outcome=outcome, event_key=key or f"evt-{uid()}", provider_ref=ref, actor="test"
        )
        return a.status, applied


# ───────────────────────────── composição das fontes ─────────────────────────────
def test_operating_balance_covers_everything(world):
    w = world(op=50_000, res=50_000)
    _, st, reason, legs, created = allocate(w, 8_000)
    assert (st, reason, legs, created) == ("RESERVED", None, {"OPERATING": 8_000}, True)
    assert view(w["op"]).free_minor == 42_000 and view(w["res"]).free_minor == 50_000


def test_partial_operating_plus_reserve_adr_example(world):
    """Obrigação de R$ 80 com R$ 30 livres → R$ 30 da operação + R$ 50 da reserva, numa única obrigação."""
    w = world(op=3_000, res=10_000)
    aid, st, _, legs, _ = allocate(w, 8_000)
    assert st == "RESERVED" and legs == {"OPERATING": 3_000, "RESERVE": 5_000}
    assert view(w["res"]).exposure_minor == 5_000
    intent = start(aid)
    assert intent["amount_minor"] == 8_000 and intent["intent_key"].startswith("pay:supplier:")


def test_retained_receivables_are_never_used(world):
    w = world(reserve=False)
    snap(w["op"], 0, retained=1_000_000)
    _, st, reason, legs, _ = allocate(w, 1_000, reserve=False)
    assert (st, reason, legs) == ("BLOCKED", "INSUFFICIENT_FUNDS", {})


def test_floor_is_protected(world):
    w = world(op_floor=2_000, op=5_000, res=0)
    _, st, reason, _, _ = allocate(w, 3_001)
    assert (st, reason) == ("BLOCKED", "RESERVE_INSUFFICIENT")  # só 3.000 acima do piso e reserva vazia
    _, st, _, legs, _ = allocate(world(op_floor=2_000, op=5_000, res=0), 3_000)
    assert st == "RESERVED" and legs == {"OPERATING": 3_000}


def test_insufficient_reserve_blocks_without_partial_payment(world):
    w = world(op=3_000, res=4_000)
    aid, st, reason, legs, _ = allocate(w, 8_000)
    assert (st, reason, legs) == ("BLOCKED", "RESERVE_INSUFFICIENT", {})
    assert view(w["op"]).committed_minor == 0 and view(w["res"]).committed_minor == 0
    with pytest.raises(funding.InvalidAllocationState):
        start(aid)


def test_reserve_requires_owner_policy(world):
    w = world(op=1_000, reserve=False)
    with session_scope() as s:
        r = funding.ensure_source(s, f"rs-{uid()}", "RESERVE", "Reserva sem política")
        w["res"] = r.id
        with pytest.raises(funding.FundingError, match="reserva exige"):
            funding.configure_source(s, r.id, floor_minor=0, approved_by="owner")
    snap(w["res"], 100_000)
    _, st, reason, _, _ = allocate(w, 5_000)
    assert (st, reason) == ("BLOCKED", "RESERVE_NOT_CONFIGURED")


def test_reserve_limits_per_order_period_and_exposure(world):
    w = world(op=0, res=100_000, per_order=4_000, period=100_000, exposure=100_000)
    assert allocate(w, 4_001)[1:3] == ("BLOCKED", "RESERVE_ORDER_LIMIT")

    w = world(op=0, res=100_000, per_order=5_000, period=8_000, exposure=100_000)
    assert allocate(w, 5_000, key=f"a-{uid()}")[1] == "RESERVED"
    assert allocate(w, 3_001, key=f"b-{uid()}")[1:3] == ("BLOCKED", "RESERVE_PERIOD_LIMIT")

    w = world(op=0, res=100_000, per_order=5_000, period=100_000, exposure=6_000)
    assert allocate(w, 5_000, key=f"a-{uid()}")[1] == "RESERVED"
    assert allocate(w, 1_001, key=f"b-{uid()}")[1:3] == ("BLOCKED", "RESERVE_EXPOSURE_LIMIT")


def test_stale_balance_does_not_fund(world, monkeypatch):
    w = world(reserve=False)
    snap(w["op"], 50_000, at=now() - dt.timedelta(seconds=get_settings().funding_balance_max_age_s + 5))
    assert allocate(w, 1_000, reserve=False)[1:3] == ("BLOCKED", "OPERATING_BALANCE_STALE")
    assert view(w["op"]).stale is True and view(w["op"]).free_minor is None


def test_currency_mismatch_is_never_summed(world):
    w = world(op=100_000, res=100_000)
    assert allocate(w, 1_000, currency="USD")[1:3] == ("BLOCKED", "CURRENCY_MISMATCH")


def test_order_and_supplier_must_be_eligible(world):
    assert allocate(world(op=10_000, payment="PENDING"), 1_000)[2] == "ORDER_NOT_ELIGIBLE"
    assert allocate(world(op=10_000, payment="CHARGEBACK"), 1_000)[2] == "ORDER_NOT_ELIGIBLE"
    assert allocate(world(op=10_000, supplier_status="EVALUATING"), 1_000)[2] == "SUPPLIER_NOT_APPROVED"


def test_expired_quote_blocks_allocation_and_payment(world):
    w = world(op=10_000)
    assert allocate(w, 1_000, valid=now() - dt.timedelta(seconds=1))[2] == "QUOTE_EXPIRED"
    w = world(op=10_000)
    aid = allocate(w, 1_000, valid=now() + dt.timedelta(seconds=1))[0]
    with session_scope() as s:
        s.execute(
            text("UPDATE funding_allocations SET quote_valid_until = now() - interval '1 minute' WHERE id=:i"),
            {"i": aid},
        )
    with pytest.raises(funding.PaymentBlocked, match="QUOTE_EXPIRED"):
        start(aid)
    assert status(aid) == "RESERVED"


def test_kill_switch_blocks_new_allocation_and_payment(world, monkeypatch):
    w = world(op=10_000)
    aid = allocate(w, 1_000)[0]
    monkeypatch.setattr(funding.killswitch, "is_active", lambda db: True)
    assert allocate(world(op=10_000), 1_000)[2] == "EMERGENCY_STOP"
    with pytest.raises(funding.PaymentBlocked, match="EMERGENCY_STOP"):
        start(aid)


# ───────────────────────────── idempotência e beneficiário ─────────────────────────────
def test_replay_returns_same_allocation_and_conflict_on_changed_payload(world):
    w = world(op=10_000)
    first = allocate(w, 1_000)
    again = allocate(w, 1_000)
    assert again[0] == first[0] and again[4] is False
    assert view(w["op"]).committed_minor == 1_000  # replay não compromete duas vezes
    with pytest.raises(funding.IdempotencyConflict):
        allocate(w, 1_000, beneficiary="pix:outra-conta")


def test_beneficiary_change_blocks_payment(world):
    w = world(op=10_000)
    aid = allocate(w, 1_000)[0]
    with pytest.raises(funding.PaymentBlocked, match="BENEFICIARY_CHANGED"):
        start(aid, beneficiary="pix:conta-trocada")
    assert status(aid) == "RESERVED"


# ───────────────────────────── concorrência ─────────────────────────────
def test_concurrent_allocations_never_overcommit(world):
    w = world(op=10_000, reserve=False)
    out, barrier = [], threading.Barrier(8)

    def worker(i):
        barrier.wait()
        out.append(allocate(w, 3_000, key=f"conc-{i}-{uid()}", reserve=False)[1])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert out.count("RESERVED") == 3 and out.count("BLOCKED") == 5
    assert view(w["op"]).committed_minor == 9_000 and view(w["op"]).free_minor == 1_000


def test_concurrent_same_obligation_has_one_live_allocation(world):
    w = world(op=10_000, reserve=False)
    key, ids, barrier = f"same-{uid()}", [], threading.Barrier(6)

    def worker():
        barrier.wait()
        ids.append(allocate(w, 2_000, key=key, reserve=False)[0])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(ids)) == 1 and view(w["op"]).committed_minor == 2_000


# ───────────────────────────── pagamento único, timeout, crash ─────────────────────────────
def test_timeout_after_debit_stays_committed_until_query(world):
    w = world(op=5_000, reserve=False)
    aid = allocate(w, 4_000, reserve=False)[0]
    start(aid)
    assert result(aid, "UNKNOWN") == ("PAYMENT_UNKNOWN", True)
    with pytest.raises(funding.PaymentOutcomeUnknown):
        start(aid)  # sem novo débito às cegas
    with pytest.raises(funding.PaymentOutcomeUnknown):
        with session_scope() as s:
            funding.release(s, aid, reason="cancelado", actor="test")
    assert allocate(w, 2_000, key=f"other-{uid()}", reserve=False)[2] == "INSUFFICIENT_FUNDS"
    assert result(aid, "SUCCEEDED", ref="PAY-123") == ("PAID", True)  # consulta confirmou o débito


def test_timeout_before_debit_frees_money_after_query(world):
    w = world(op=5_000, reserve=False)
    aid = allocate(w, 4_000, reserve=False)[0]
    start(aid)
    result(aid, "UNKNOWN")
    assert result(aid, "FAILED") == ("FAILED", True)  # consulta confirmou que não houve débito
    assert view(w["op"]).committed_minor == 0
    retry = allocate(w, 4_000, reserve=False)
    assert retry[1] == "RESERVED" and retry[0] != aid  # nova alocação, nova leitura de saldo


def test_crash_after_start_is_listed_for_reconciliation(world):
    w = world(op=5_000, reserve=False)
    aid = allocate(w, 1_000, reserve=False)[0]
    start(aid)  # processo "morre" aqui: transação confirmada, resultado nunca chegou
    with session_scope() as s:
        assert aid in {a.id for a in funding.pending_reconciliation(s)}
    with pytest.raises(funding.PaymentOutcomeUnknown):
        start(aid)


def test_duplicate_and_out_of_order_provider_events(world):
    w = world(op=5_000, reserve=False)
    aid = allocate(w, 1_000, reserve=False)[0]
    start(aid)
    assert result(aid, "SUCCEEDED", key="wh-1", ref="PAY-1") == ("PAID", True)
    assert result(aid, "SUCCEEDED", key="wh-1", ref="PAY-1") == ("PAID", False)  # webhook duplicado
    with pytest.raises(funding.IdempotencyConflict):
        result(aid, "FAILED", key="wh-1")
    assert result(aid, "FAILED", key="wh-late") == ("PAID", False)  # fora de ordem não desfaz pagamento
    with session_scope() as s:
        types = s.execute(select(FundingEvent.event_type).where(FundingEvent.allocation_id == aid)).scalars().all()
    assert types.count("PAYMENT_SUCCEEDED") == 1 and "RECONCILIATION_CONFLICT" in types


def test_paid_debit_is_not_subtracted_twice_after_new_reading(world):
    w = world(op=5_000, reserve=False)
    aid = allocate(w, 1_000, reserve=False)[0]
    with session_scope() as s:
        funding.pay_simulated(s, aid, actor="test")
    assert view(w["op"]).free_minor == 4_000  # leitura antiga ainda não reflete o débito
    snap(w["op"], 4_000)  # nova leitura já reflete
    assert view(w["op"]).committed_minor == 0 and view(w["op"]).free_minor == 4_000


# ───────────────────────────── cancelamento, chargeback, recebível ─────────────────────────────
def test_cancellation_before_payment_releases_funds(world):
    w = world(op=1_000, res=10_000)
    aid = allocate(w, 3_000)[0]
    with session_scope() as s:
        funding.release(s, aid, reason="pedido cancelado", actor="test")
    assert view(w["res"]).committed_minor == 0 and view(w["res"]).exposure_minor == 0
    with pytest.raises(funding.InvalidAllocationState):
        start(aid)


def test_chargeback_after_payment_does_not_free_committed_money(world):
    w = world(op=1_000, res=10_000)
    aid = allocate(w, 3_000)[0]
    with session_scope() as s:
        funding.pay_simulated(s, aid, actor="test")
        s.get(Order, w["order"]).payment_status = "CHARGEBACK"
    with pytest.raises(funding.InvalidAllocationState, match="já paga"):
        with session_scope() as s:
            funding.release(s, aid, reason="chargeback", actor="test")
    assert view(w["res"]).exposure_minor == 2_000


def test_later_receivable_reduces_exposure_without_second_payment(world):
    w = world(op=3_000, res=10_000)
    aid = allocate(w, 8_000)[0]
    with session_scope() as s:
        funding.pay_simulated(s, aid, actor="test")
    rec = dict(currency="BRL", external_ref="ML-SETTLE-1", idempotency_key=f"rcv-{uid()}", actor="test")
    with session_scope() as s:
        out = funding.recognize_receivable(s, aid, amount_minor=20_000, **rec)
    assert out == {"recovered_minor": 5_000, "replayed": False, "exposure_minor": 0}  # limitado à reserva usada
    with session_scope() as s:
        assert funding.recognize_receivable(s, aid, amount_minor=20_000, **rec)["replayed"] is True
        paid = s.execute(
            select(func.count())
            .select_from(FundingEvent)
            .where(FundingEvent.allocation_id == aid, FundingEvent.event_type == "PAYMENT_SUCCEEDED")
        ).scalar_one()
        allocations = s.execute(
            select(func.count()).select_from(FundingAllocation).where(FundingAllocation.order_id == w["order"])
        ).scalar_one()
    assert (paid, allocations) == (1, 1)
    assert view(w["res"]).exposure_minor == 0


# ───────────────────────────── guardas ─────────────────────────────
def test_live_mode_refuses_funding(world, monkeypatch):
    w = world(op=10_000)
    monkeypatch.setattr(get_settings(), "mode", RuntimeMode.LIVE)
    with pytest.raises(funding.FundingDisabled):
        allocate(w, 1_000)


def test_snapshot_rules(world):
    w = world(reserve=False)
    key = f"snap-{uid()}"
    at = now()
    first = snap(w["op"], 1_000, at=at, key=key)
    assert snap(w["op"], 1_000, at=at, key=key) == first
    with pytest.raises(funding.IdempotencyConflict):
        snap(w["op"], 2_000, at=at, key=key)
    with pytest.raises(funding.FundingError, match="futuro"):
        snap(w["op"], 1_000, at=now() + dt.timedelta(hours=1))
    with session_scope() as s, pytest.raises(funding.FundingError, match="incompatível"):
        funding.record_snapshot(
            s,
            w["op"],
            available_minor=1,
            currency="BRL",
            observed_at=now(),
            origin="STATEMENT_IMPORT",
            idempotency_key=f"x-{uid()}",
            actor="test",
        )


def test_trail_tables_are_append_only(world):
    w = world(op=10_000)
    allocate(w, 1_000)
    for table in ("funding_legs", "funding_events", "cash_balance_snapshots"):
        with pytest.raises(DBAPIError, match="append-only"):
            with session_scope() as s:
                s.execute(text(f"UPDATE {table} SET created_at = now()"))
