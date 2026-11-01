# ruff: noqa: F811  (fixtures pytest importadas: shop)
"""COM-008 (campanhas, métricas, gasto × ledger) e GOV-005 (pausas externas e relatório pós-stop)."""

import datetime as dt
import uuid

from sqlalchemy import select

from aicommerce.db import session_scope
from aicommerce.finance import ledger
from aicommerce.models import Campaign, ChannelListing
from tests.test_approvals import decide, drain, submit
from tests.test_orders import shop  # noqa: F401  (fixture shop)


def campaign(client, op, owner, acc, *, daily=1_000, days=3):
    prop = submit(
        client,
        op,
        action_type="CREATE_CAMPAIGN",
        agent_id="pixel",
        subject_type="campaign",
        subject_id=f"CAMP-{uuid.uuid4().hex[:6]}",
        payload={"name": "Teste garrafa (SIMULADO)", "daily_budget_minor": daily, "days": days},
        monetary_scope_minor=daily * days,
        budget_account_id=str(acc),
        go_live=None,
    )
    assert decide(client, owner, prop).status_code == 200
    drain()
    with session_scope() as db:
        c = db.execute(select(Campaign).where(Campaign.proposal_id == uuid.UUID(prop["id"]))).scalar_one()
        return str(c.id)


def alerts_of(client, h, ref):
    return {a["kind"] for a in client.get("/v1/alerts", headers=h).json()["items"] if a["ref"] == ref}


def test_campaign_created_on_execution_and_spend_reconciled(client, auth, budget_account):
    op, owner, fin = auth("operator"), auth("owner"), auth("finance")
    acc = budget_account(cap=10_000, hold=0)
    cid = campaign(client, op, owner, acc)
    c = client.get(f"/v1/campaigns/{cid}", headers=fin).json()
    assert c["status"] == "ACTIVE" and c["cap_minor"] == 3_000 and c["simulated"] and c["external_id"]

    r = client.post(f"/v1/sim/campaigns/{cid}/metrics", headers=op, json={"days": 2}).json()
    c = r["campaign"]
    assert r["imported"] == 2 and 1_600 <= c["spend_minor"] <= 2_000
    assert c["reconciled"] and c["ledger_spend_minor"] == c["spend_minor"]
    assert c["ctr_bps"] and c["cpc_minor"] is not None
    with session_scope() as db:
        v = ledger.budget_view(db, acc)
        assert v.reconciled_spend_minor == c["spend_minor"]
        assert v.available_minor == 10_000 - 3_000  # gasto consome a reserva, não a soma de novo

    day = c["days"][0]
    same = {k: day[k] for k in ("impressions", "clicks", "conversions", "revenue_minor", "spend_minor")}
    rep = client.post(f"/v1/campaigns/{cid}/metrics", headers=fin, json={"day": day["day"], **same})
    assert rep.status_code == 201 and rep.json()["replayed"] is True
    # Idempotência do dia exige que TODAS as métricas sejam iguais, não só o gasto.
    # Ignorar alteração de conversões/receita corromperia o ROAS silenciosamente.
    for metric in ("impressions", "clicks", "conversions", "revenue_minor", "spend_minor"):
        altered = {**same, metric: same[metric] + 1}
        diff = client.post(f"/v1/campaigns/{cid}/metrics", headers=fin, json={"day": day["day"], **altered})
        assert diff.status_code == 422, (metric, diff.text)
    unchanged = client.get(f"/v1/campaigns/{cid}", headers=fin).json()
    assert unchanged["spend_minor"] == c["spend_minor"]
    assert unchanged["revenue_minor"] == c["revenue_minor"]
    assert unchanged["ledger_spend_minor"] == c["ledger_spend_minor"]
    bad = client.post(
        f"/v1/campaigns/{cid}/metrics",
        headers=fin,
        json={"day": "2026-12-01", "impressions": 1, "clicks": 5, "spend_minor": 0},
    )
    assert bad.status_code == 422
    assert (
        client.post(
            f"/v1/campaigns/{cid}/metrics",
            headers=auth("viewer"),
            json={"day": "2026-12-01", "impressions": 1, "clicks": 0, "spend_minor": 0},
        ).status_code
        == 403
    )


def test_overspend_is_recorded_and_alerted(client, auth, budget_account):
    op, owner, fin = auth("operator"), auth("owner"), auth("finance")
    acc = budget_account(cap=10_000, hold=0)
    cid = campaign(client, op, owner, acc, daily=500, days=2)  # teto 1.000
    today = dt.date.today()
    r = client.post(
        f"/v1/campaigns/{cid}/metrics",
        headers=fin,
        json={"day": today.isoformat(), "impressions": 900, "clicks": 9, "spend_minor": 1_300},
    )
    assert r.status_code == 201 and r.json()["overspend"] is True
    c = r.json()["campaign"]
    assert c["status"] == "ENDED" and c["spend_minor"] == 1_300 and c["reconciled"]
    assert "CAMPAIGN_OVERSPEND" in alerts_of(client, op, f"campaign:{cid}")


def test_pause_goes_through_approval_and_stops_spend(client, auth, budget_account):
    op, owner, fin = auth("operator"), auth("owner"), auth("finance")
    cid = campaign(client, op, owner, budget_account(cap=10_000, hold=0))
    prop = client.post(f"/v1/campaigns/{cid}/pause", headers=op, json={"reason": "CPC alto"}).json()
    assert prop["action_type"] == "PAUSE_MEDIA" and prop["status"] == "PENDING_APPROVAL"  # sem delegação
    assert client.get(f"/v1/campaigns/{cid}", headers=fin).json()["status"] == "PAUSE_REQUESTED"
    assert decide(client, owner, prop).status_code == 200
    drain()
    assert client.get(f"/v1/campaigns/{cid}", headers=fin).json()["status"] == "PAUSED"
    assert client.post(f"/v1/sim/campaigns/{cid}/metrics", headers=op, json={"days": 2}).json()["imported"] == 0
    assert client.post(f"/v1/campaigns/{cid}/pause", headers=op, json={"reason": "de novo"}).status_code == 422


def test_emergency_stop_requests_pauses_and_reports_real_outcome(client, auth, budget_account, shop):
    op, owner = auth("operator"), auth("owner")
    cid = campaign(client, op, owner, budget_account(cap=10_000, hold=0))
    lid = shop["listing"]["id"]
    with session_scope() as db:
        assert db.get(ChannelListing, uuid.UUID(lid)).status == "PUBLISHED"
    r = client.post("/v1/emergency-stop", json={"reason": "teste GOV-005"}, headers=owner)
    try:
        assert r.status_code == 200
        reqs = {x["subject_id"]: x for x in r.json()["external_pause_requests"]}
        assert reqs[lid]["kind"] == "LISTING" and reqs[lid]["requested_status"] == "AUTHORIZED"
        assert reqs[cid]["kind"] == "CAMPAIGN" and reqs[cid]["requested_status"] == "PENDING_APPROVAL"

        rep = client.get("/v1/emergency-stop/report", headers=owner).json()
        mine = {p["subject_id"]: p for p in rep["pauses"]}
        assert mine[lid]["outcome"] == "PENDENTE"  # pedido não é confirmação
        assert "CAMPAIGN_ACTIVE_DURING_STOP" in alerts_of(client, op, f"campaign:{cid}")

        drain()  # executor roda PAUSE_* mesmo com o stop
        with session_scope() as db:
            assert db.get(ChannelListing, uuid.UUID(lid)).status == "PAUSED"
        rep = client.get("/v1/emergency-stop/report", headers=owner).json()
        mine = {p["subject_id"]: p for p in rep["pauses"]}
        assert mine[lid]["outcome"] == "CONFIRMADA" and mine[cid]["outcome"] == "PENDENTE"

        camp_prop = {"id": reqs[cid]["proposal_id"]}
        detail = client.get(f"/v1/approvals/{camp_prop['id']}", headers=owner).json()
        assert decide(client, owner, detail).status_code == 200  # humano aprova a pausa de mídia
        drain()
        rep = client.get("/v1/emergency-stop/report", headers=owner).json()
        mine = {p["subject_id"]: p for p in rep["pauses"]}
        assert mine[cid]["outcome"] == "CONFIRMADA"
        assert client.get("/v1/emergency-stop/report", headers=auth("viewer")).status_code == 403
    finally:
        client.post("/v1/emergency-stop/release", json={"reason": "fim do teste GOV-005"}, headers=owner)
    after = client.get("/v1/emergency-stop/report", headers=owner).json()
    assert after["active"] is False and after["released_at"] and after["pauses"]  # histórico preservado
