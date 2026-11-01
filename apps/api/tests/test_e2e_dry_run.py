"""QA-001 — E2E da missão completa em DRY_RUN (SDD §18):
research → supplier → dossier → proposal → human approval → publish → verify external → pedido → fiscal →
compra no fornecedor → envio → entrega → devolução → reembolso → repasse → conciliação; auditoria íntegra e
todo equivalente LIVE negado. Gera progress/test-reports/e2e_dry_run.json."""

import datetime as dt
import json
import pathlib

from sqlalchemy import func, select

from aicommerce.ai import runtime
from aicommerce.commerce import orders, webhooks
from aicommerce.db import session_scope
from aicommerce.governance import audit, executor
from aicommerce.models import ApprovalProposal, ExecutionJob
from aicommerce.providers.registry import resolve
from aicommerce.worker import run_once
from tests.test_approvals import decide
from tests.test_suppliers import approve_supplier, make_ready, mk_product, mk_supplier

REPORT = pathlib.Path(__file__).resolve().parents[3] / "progress" / "test-reports" / "e2e_dry_run.json"


def drain():
    while (
        run_once()
        or runtime.advance_missions()
        or executor.execute_next()
        or executor.reconcile_next()
        or orders.process_fiscal_next()
        or webhooks.process_next()
    ):
        pass


def test_full_mission_dry_run(client, auth):
    op, owner, fin = auth("operator"), auth("owner"), auth("finance")
    steps: list[dict] = []

    def step(name, ok, **info):
        steps.append({"step": name, "ok": bool(ok), **info})
        assert ok, f"{name}: {info}"

    # 1. pesquisa
    t = client.post(
        "/v1/agent-tasks",
        headers=op,
        json={"agent_id": "luna", "kind": "research.opportunities", "input": {"topic": "garrafa térmica"}},
    ).json()
    drain()
    res = client.get(f"/v1/agent-tasks/{t['id']}", headers=op).json()
    step(
        "research",
        res["state"] == "COMPLETED" and res["result"]["opportunity_ids"],
        opportunities=len(res["result"]["opportunity_ids"]),
        model=res["result"]["model"],
    )
    # 2. fornecedor + dossiê
    prod = mk_product(client, op)
    sup = mk_supplier(client, op)
    offer = make_ready(client, op, owner, sup, prod["variants"][0]["id"])
    s = approve_supplier(client, owner, sup["id"])
    step(
        "supplier_dossier",
        s["status"] == "APPROVED" and s["assessment"]["ready_for_approval"],
        risk=s["assessment"]["level"],
    )
    # 3. anúncio + proposta + aprovação humana + publicação
    x = client.post(
        "/v1/channels/WOOCOMMERCE/listings/prepare",
        headers=op,
        json={"variant_id": prod["variants"][0]["id"], "offer_id": offer["id"], "shipping_minor": 1500},
    ).json()
    step("listing_prepared", x["blockers"] == [], price_minor=x["price_minor"])
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op).json()
    step("proposal_pending", prop["status"] == "PENDING_APPROVAL", policy=prop["policy_version"])
    step("human_approval", decide(client, owner, prop).status_code == 200)
    drain()
    listing = client.get(f"/v1/listings/{x['id']}", headers=op).json()
    step("published", listing["status"] == "PUBLISHED", external_id=listing["external_id"])
    # 4. verificação externa: o provedor confirma o efeito pela chave idempotente
    with session_scope() as db:
        job = db.execute(
            select(ExecutionJob).where(
                ExecutionJob.proposal_id == ApprovalProposal.id, ApprovalProposal.id == prop["id"]
            )
        ).scalar_one()
        found = resolve("PUBLISH_LISTING", "DRY_RUN", "WOOCOMMERCE").provider.lookup(job.idempotency_key)
    step("verify_external", found is not None and found.provider_ref == listing["external_id"])
    # 5. pedido → fiscal → compra → envio → entrega
    o = client.post("/v1/sim/orders", headers=op, json={"listing_id": x["id"], "qty": 1}).json()
    drain()
    o = client.get(f"/v1/orders/{o['id']}", headers=op).json()
    step(
        "order_fiscal",
        o["status"] == "READY_TO_FULFILL" and o["fiscal_documents"][0]["status"] == "AUTHORIZED_SIMULATED",
    )
    props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=op).json()["proposals"]
    decide(client, owner, props[0])
    drain()
    f = client.get(f"/v1/orders/{o['id']}", headers=op).json()["fulfillments"][0]
    client.post(f"/v1/sim/fulfillments/{f['id']}/ship", headers=op)
    client.post(f"/v1/sim/fulfillments/{f['id']}/deliver", headers=op)
    o = client.get(f"/v1/orders/{o['id']}", headers=op).json()
    step("delivered", o["status"] == "DELIVERED")
    # 6. repasse e conciliação (antes do reembolso: venda conciliada)
    client.post("/v1/sim/settlements", headers=op, json={"channel": "WOOCOMMERCE"})
    rec = client.get("/v1/finance/reconciliation?channel=WOOCOMMERCE", headers=fin).json()
    step("reconciled", any(m["order_external_id"] == o["external_id"] for m in rec["matched"]))
    # 7. devolução por arrependimento → reembolso aprovado
    ret = client.post(f"/v1/orders/{o['id']}/returns", headers=op, json={"reason": "ARREPENDIMENTO"}).json()
    decide(client, owner, ret["proposal"])
    drain()
    o = client.get(f"/v1/orders/{o['id']}", headers=op).json()
    step("refunded", o["status"] == "REFUNDED" and ret["withdrawal_right"] is True)
    # 8. trilha de auditoria íntegra e nada executado sem aprovação
    with session_scope() as db:
        ok, _ = audit.verify_chain(db)
        unapproved = db.execute(
            select(func.count())
            .select_from(ExecutionJob)
            .join(ApprovalProposal, ApprovalProposal.id == ExecutionJob.proposal_id)
            .where(
                ApprovalProposal.status.in_(("PENDING_APPROVAL", "REJECTED", "DENIED")),
                ExecutionJob.status == "SUCCEEDED",
            )
        ).scalar_one()
    step("audit_chain_intact", ok)
    step("zero_unapproved_executions", unapproved == 0)
    # 9. go-live negado em todos os canais (gates pendentes)
    for ch in ("WOOCOMMERCE", "MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP"):
        d = client.post(
            "/v1/go-live/evaluate",
            headers=op,
            json={"channel": ch, "supplier_origin": "NATIONAL", "fulfillment_mode": "DROPSHIP_SUPPLIER", "uf": "SP"},
        ).json()
        step(f"golive_denied_{ch}", d["decision"] == "DENY")
    REPORT.write_text(
        json.dumps(
            {
                "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "ok": True,
                "mode": "DRY_RUN",
                "steps": steps,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
