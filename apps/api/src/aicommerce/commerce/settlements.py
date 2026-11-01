"""Repasses dos canais e conciliação financeira (COM-012; SDD §8 reconciliação).

Esperado por pedido = total − (comissão + meio de pagamento, em bps sobre o total) − tarifa fixa, com a tabela de
tarifas vigente do canal. Conciliação casa linhas de repasse com pedidos por external_id e reporta:
matched, divergentes (diferença ≠ 0), pagos sem repasse após N dias (recebível em aberto) e linhas órfãs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicommerce.commerce.pricing import pct_of
from aicommerce.config import get_settings
from aicommerce.governance import audit
from aicommerce.models import ChannelFeeSchedule, Order, Settlement, SettlementLine
from aicommerce.office import events

OVERDUE_DAYS = 30


class SettlementError(Exception):
    code = "SETTLEMENT_ERROR"
    http = 409


def expected_fee(db: Session, channel: str, gross_minor: int) -> int:
    fs = (
        db.execute(
            select(ChannelFeeSchedule)
            .where(ChannelFeeSchedule.channel == channel, ChannelFeeSchedule.is_active.is_(True))
            .order_by(ChannelFeeSchedule.version.desc())
        )
        .scalars()
        .first()
    )
    if fs is None:
        return 0
    return pct_of(gross_minor, fs.commission_bps + fs.payment_fee_bps) + fs.fixed_fee_minor


# Linhas chegam com valores positivos; o tipo define o sentido. Tarifa é sempre custo (negativa = comissão devolvida).
SIGN = {"SALE": 1, "REFUND": -1, "CHARGEBACK": -1, "CHARGEBACK_REVERSAL": 1}


def line_net(kind: str, gross_minor: int, fee_minor: int) -> int:
    return SIGN[kind] * gross_minor - fee_minor


def import_settlement(
    db: Session,
    *,
    channel: str,
    external_id: str,
    period_start: dt.datetime,
    period_end: dt.datetime,
    lines: list[dict[str, Any]],
    actor: str,
    simulated: bool,
) -> tuple[Settlement, bool]:
    existing = db.execute(
        select(Settlement).where(Settlement.channel == channel, Settlement.external_id == external_id)
    ).scalar_one_or_none()
    if existing:
        return existing, False
    for x in lines:
        x["kind"] = x.get("kind", "SALE")
        if x["kind"] not in SIGN:
            raise SettlementError(f"tipo de linha desconhecido: {x['kind']}")
        if int(x["gross_minor"]) < 0:
            raise SettlementError("gross_minor deve ser positivo; o sentido vem do tipo (kind)")
    gross = sum(SIGN[x["kind"]] * int(x["gross_minor"]) for x in lines)
    fees = sum(int(x["fee_minor"]) for x in lines)
    s = Settlement(
        channel=channel,
        external_id=external_id,
        period_start=period_start,
        period_end=period_end,
        gross_minor=gross,
        fees_minor=fees,
        net_minor=gross - fees,
        is_simulated=simulated,
    )
    db.add(s)
    db.flush()
    for x in lines:
        db.add(
            SettlementLine(
                settlement_id=s.id,
                order_external_id=str(x["order_external_id"]),
                gross_minor=int(x["gross_minor"]),
                fee_minor=int(x["fee_minor"]),
                net_minor=line_net(x["kind"], int(x["gross_minor"]), int(x["fee_minor"])),
                kind=x["kind"],
            )
        )
    db.flush()
    _apply_disputes(db, channel, lines, actor)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="settlement.imported",
        subject_type="settlement",
        subject_id=str(s.id),
        payload={"channel": channel, "external_id": external_id, "lines": len(lines), "net_minor": s.net_minor},
    )
    events.emit(
        db,
        "settlement.imported",
        agent_id="finn",
        payload={"settlement_id": str(s.id), "channel": channel, "net_minor": s.net_minor, "simulated": simulated},
    )
    return s, True


def _apply_disputes(db: Session, channel: str, lines: list[dict[str, Any]], actor: str) -> None:
    """Chargeback só aparece no extrato: marca o pedido em disputa; reversão (disputa ganha) restaura."""
    for x in lines:
        if x["kind"] not in ("CHARGEBACK", "CHARGEBACK_REVERSAL"):
            continue
        o = db.execute(
            select(Order).where(Order.channel == channel, Order.external_id == str(x["order_external_id"]))
        ).scalar_one_or_none()
        if o is None:
            continue  # vira órfão na conciliação
        before = o.payment_status
        o.payment_status = "CHARGEBACK" if x["kind"] == "CHARGEBACK" else "CAPTURED"
        audit.record(
            db,
            actor_type="user",
            actor_id=actor,
            action="order.chargeback.opened" if x["kind"] == "CHARGEBACK" else "order.chargeback.reversed",
            subject_type="order",
            subject_id=str(o.id),
            payload={"before": before, "amount_minor": int(x["gross_minor"]), "fee_minor": int(x["fee_minor"])},
        )


def simulate_settlement(db: Session, channel: str, *, actor: str, fee_error_minor: int = 0) -> Settlement:
    """DRY_RUN: gera o extrato que o canal enviaria para pedidos pagos ainda não repassados."""
    if not get_settings().is_dry_run:
        raise SettlementError("simulação de repasse só existe em DRY_RUN")
    settled = set(
        db.execute(
            select(SettlementLine.order_external_id)
            .join(Settlement, Settlement.id == SettlementLine.settlement_id)
            .where(Settlement.channel == channel)
        ).scalars()
    )
    orders = (
        db.execute(
            select(Order).where(
                Order.channel == channel, Order.payment_status.in_(("CAPTURED",)), Order.status != "CANCELLED"
            )
        )
        .scalars()
        .all()
    )
    lines = [
        {
            "order_external_id": o.external_id,
            "gross_minor": o.total_minor,
            "fee_minor": expected_fee(db, channel, o.total_minor) + fee_error_minor,
        }
        for o in orders
        if o.external_id not in settled
    ]
    if not lines:
        raise SettlementError("nenhum pedido pago pendente de repasse")
    now = dt.datetime.now(dt.UTC)
    s, _ = import_settlement(
        db,
        channel=channel,
        external_id=f"SIM-{uuid.uuid4().hex[:10]}",
        period_start=now - dt.timedelta(days=7),
        period_end=now,
        lines=lines,
        actor=actor,
        simulated=True,
    )
    return s


def reconcile(db: Session, channel: str | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """Concilia por pedido somando todas as linhas (venda, estornos, chargebacks).

    Esperado = líquido da venda (tabela de tarifas) − reembolsos concluídos no sistema. Pedido em chargeback não é
    "divergente": é disputa, com o valor debitado em risco até a reversão.
    """
    from aicommerce.models import ReturnRequest

    now = now or dt.datetime.now(dt.UTC)
    q = select(Order).where(Order.payment_status.in_(("CAPTURED", "REFUNDED", "CHARGEBACK")))
    if channel:
        q = q.where(Order.channel == channel)
    orders = {(o.channel, o.external_id): o for o in db.execute(q).scalars()}
    lq = select(SettlementLine, Settlement.channel).join(Settlement, Settlement.id == SettlementLine.settlement_id)
    if channel:
        lq = lq.where(Settlement.channel == channel)
    matched, divergent, orphans, disputes = [], [], [], []
    per_order: dict[tuple[str, str], list[SettlementLine]] = {}
    for line, ch in db.execute(lq).all():
        key = (ch, line.order_external_id)
        if key not in orders:
            orphans.append(
                {
                    "channel": ch,
                    "order_external_id": line.order_external_id,
                    "net_minor": line.net_minor,
                    "kind": line.kind,
                }
            )
            continue
        per_order.setdefault(key, []).append(line)
    settled_keys = set(per_order)
    for key, lines in per_order.items():
        o = orders[key]
        settled_net = sum(x.net_minor for x in lines)
        kinds = sorted({x.kind for x in lines})
        if o.payment_status == "CHARGEBACK":
            debited = sum(x.gross_minor + x.fee_minor for x in lines if x.kind == "CHARGEBACK")
            disputes.append(
                {
                    "order_id": str(o.id),
                    "channel": key[0],
                    "order_external_id": o.external_id,
                    "at_risk_minor": debited,
                    "settled_net_minor": settled_net,
                }
            )
            continue
        refunded = db.execute(
            select(func.coalesce(func.sum(ReturnRequest.refund_minor), 0)).where(
                ReturnRequest.order_id == o.id, ReturnRequest.status == "REFUNDED"
            )
        ).scalar_one()
        expected_net = o.total_minor - expected_fee(db, key[0], o.total_minor) - int(refunded)
        diff = settled_net - expected_net
        row = {
            "order_id": str(o.id),
            "channel": key[0],
            "order_external_id": o.external_id,
            "expected_net_minor": expected_net,
            "settled_net_minor": settled_net,
            "diff_minor": diff,
            "kinds": kinds,
        }
        if o.payment_status == "REFUNDED" and "REFUND" not in kinds:
            row["note"] = "reembolso concluído no sistema ainda não debitado pelo canal"
        (matched if diff == 0 else divergent).append(row)
    open_receivables = [
        {
            "order_id": str(o.id),
            "channel": o.channel,
            "order_external_id": o.external_id,
            "expected_net_minor": o.total_minor - expected_fee(db, o.channel, o.total_minor),
            "days": (now - o.placed_at).days,
            "overdue": (now - o.placed_at).days > OVERDUE_DAYS,
        }
        for key, o in orders.items()
        if key not in settled_keys and o.payment_status == "CAPTURED" and o.status != "CANCELLED"
    ]
    return {
        "matched": matched,
        "divergent": divergent,
        "orphans": orphans,
        "disputes": disputes,
        "open_receivables": open_receivables,
        "totals": {
            "matched": len(matched),
            "divergent": len(divergent),
            "orphans": len(orphans),
            "disputes": len(disputes),
            "at_risk_minor": sum(d["at_risk_minor"] for d in disputes),
            "receivable_open_minor": sum(r["expected_net_minor"] for r in open_receivables),
            "divergence_minor": sum(r["diff_minor"] for r in divergent),
        },
        "note": "Tarifas esperadas vêm da tabela vigente do canal (valores fictícios em DRY_RUN).",
    }
