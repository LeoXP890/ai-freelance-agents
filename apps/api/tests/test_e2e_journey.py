"""Jornada completa em DRY_RUN (finalização do desenvolvimento §8), na ordem em que o proprietário usaria o app:
missão → Atlas coordena Luna, Nexus, Finn, Clara e Nova → fornecedor simulado → Dante analisa a viabilidade → anúncio
preparado (preço/margem em código) → proposta → aprovação COM MFA (sem MFA é recusada) → publicação no canal
simulado → pedido → pagamento → fiscal simulado → compra no fornecedor → envio/entrega → repasse → indicadores
financeiros (contribuição = líquida − custo, conferida contra os números do pedido) → histórico, auditoria e relatório.
Tudo marcado como simulado. Gera progress/test-reports/e2e_journey.json (passos e valores)."""

from __future__ import annotations

import datetime as dt
import json
import pathlib

from aicommerce.commerce.settlements import expected_fee
from aicommerce.db import session_scope
from aicommerce.governance import audit
from aicommerce.identity.security import issue_access_token
from tests.test_approvals import decide
from tests.test_e2e_dry_run import drain
from tests.test_suppliers import approve_supplier, make_ready, mk_product, mk_supplier

REPORT = pathlib.Path(__file__).resolve().parents[3] / "progress" / "test-reports" / "e2e_journey.json"
CH = "WOOCOMMERCE"


def channel_row(client, h):
    d = client.get("/v1/dashboard/sales?days=30", headers=h).json()
    return next((c for c in d["channels"] if c["channel"] == CH), None) or {
        k: 0 for k in ("orders", "revenue_minor", "fees_minor", "cost_minor", "refund_minor", "contribution_minor")
    }, d


def test_owner_journey_mission_to_finance(client, auth, make_user):
    op, owner, fin = auth("operator"), auth("owner"), auth("finance")
    steps: list[dict] = []

    def step(name, ok, **info):
        steps.append({"step": name, "ok": bool(ok), **info})
        assert ok, f"{name}: {info}"

    # 1–2. missão: o Atlas planeja e coordena cinco agentes (fan-out 5)
    r = client.post(
        "/v1/missions", headers=op, json={"goal": "avaliar garrafa térmica para a loja própria", "max_steps": 5}
    )
    step("mission_created", r.status_code == 201, status=r.status_code)
    mid = r.json()["mission_id"]
    drain()
    m = client.get(f"/v1/missions/{mid}", headers=op).json()
    agents = sorted({c["agent_id"] for c in m["children"]})
    step("mission_completed", m["state"] == "COMPLETED", state=m["state"])
    step("atlas_coordinated", agents == ["clara", "finn", "luna", "nexus", "nova"], agents=agents)
    step("children_done", all(c["state"] == "COMPLETED" for c in m["children"]), n=len(m["children"]))
    step("mission_simulated_and_costed", m["simulated"] is True and m["cost_micro_usd"] > 0, cost=m["cost_micro_usd"])
    # 3–4. Luna trouxe oportunidades simuladas; Nexus avalia o fornecedor simulado (dossiê aprovado pelo humano)
    luna = client.get(
        f"/v1/agent-tasks/{next(c['id'] for c in m['children'] if c['agent_id'] == 'luna')}", headers=op
    ).json()
    step("luna_research", luna["result"]["opportunity_ids"], opportunities=len(luna["result"]["opportunity_ids"]))
    prod = mk_product(client, op)
    sup = mk_supplier(client, op)
    offer = make_ready(client, op, owner, sup, prod["variants"][0]["id"])
    s = approve_supplier(client, owner, sup["id"])
    step("supplier_approved", s["status"] == "APPROVED", risk=s["assessment"]["level"])
    # 5. Dante analisa a viabilidade da oferta (preço/margem determinísticos)
    t = client.post(
        "/v1/agent-tasks",
        headers=op,
        json={"agent_id": "dante", "kind": "commercial.model", "input": {"offer_id": offer["id"]}},
    ).json()
    drain()
    dante = client.get(f"/v1/agent-tasks/{t['id']}", headers=op).json()
    step("dante_viability", dante["state"] == "COMPLETED" and dante["simulated"], state=dante["state"])
    # 6–7. anúncio preparado (Finn: preço e margem calculados em código) — Nova cuida do canal
    x = client.post(
        f"/v1/channels/{CH}/listings/prepare",
        headers=op,
        json={"variant_id": prod["variants"][0]["id"], "offer_id": offer["id"], "shipping_minor": 1500},
    ).json()
    step("listing_prepared", x["blockers"] == [] and x["price_minor"] > 0, price_minor=x["price_minor"])
    # 8. Clara: revisão de regras sobre o anúncio já está na missão; a política de risco avalia a proposta
    clara = next(c for c in m["children"] if c["agent_id"] == "clara")
    step("clara_rules", clara["state"] == "COMPLETED")
    # 9–10. proposta → aprovação: sem MFA é recusada; com MFA o servidor registra
    prop = client.post(f"/v1/listings/{x['id']}/propose-publish", headers=op).json()
    step("proposal_pending", prop["status"] == "PENDING_APPROVAL" and prop["simulated"], policy=prop["policy_version"])
    row = next(p for p in client.get("/v1/approvals", headers=owner).json()["items"] if p["id"] == prop["id"])
    step(
        "inbox_identifies",
        row["summary"]["object"] == "Garrafa teste" and row["summary"]["channel"] == CH,
        summary=row["summary"],
    )
    u = make_user("owner")
    no_mfa = {"Authorization": f"Bearer {issue_access_token(u['id'], mfa=False)}"}
    step("approval_without_mfa_refused", decide(client, no_mfa, prop).status_code == 403)
    step("approval_with_mfa", decide(client, owner, prop).status_code == 200)
    # 11. publicação no canal simulado
    drain()
    listing = client.get(f"/v1/listings/{x['id']}", headers=op).json()
    step("published_simulated", listing["status"] == "PUBLISHED", external_id=listing["external_id"])
    # 12–13. pedido simulado + pagamento + fiscal simulado
    before, _ = channel_row(client, fin)
    o = client.post("/v1/sim/orders", headers=op, json={"listing_id": x["id"], "qty": 2}).json()
    drain()
    o = client.get(f"/v1/orders/{o['id']}", headers=op).json()
    step("order_paid", o["payment_status"] == "CAPTURED", payment=o["payment_status"])
    step("fiscal_simulated", o["fiscal_documents"][0]["status"] == "AUTHORIZED_SIMULATED")
    # 14. logística simulada: compra no fornecedor (aprovada) → envio → entrega
    props = client.post(f"/v1/orders/{o['id']}/fulfill", headers=op).json()["proposals"]
    step("purchase_approved", decide(client, owner, props[0]).status_code == 200, cost=props[0]["monetary_scope_minor"])
    drain()
    f = client.get(f"/v1/orders/{o['id']}", headers=op).json()["fulfillments"][0]
    client.post(f"/v1/sim/fulfillments/{f['id']}/ship", headers=op)
    client.post(f"/v1/sim/fulfillments/{f['id']}/deliver", headers=op)
    o = client.get(f"/v1/orders/{o['id']}", headers=op).json()
    step("delivered", o["status"] == "DELIVERED")
    client.post("/v1/sim/settlements", headers=op, json={"channel": CH})
    rec = client.get(f"/v1/finance/reconciliation?channel={CH}", headers=fin).json()
    step("reconciled", any(r["order_external_id"] == o["external_id"] for r in rec["matched"]))
    # 15. indicadores: o que o pedido acrescentou bate com a definição documentada
    after, dash = channel_row(client, fin)
    cost = sum(i["unit_cost_minor"] * i["qty"] for i in o["items"])
    with session_scope() as db:
        fee = expected_fee(db, CH, o["total_minor"])
    delta = {k: after[k] - before[k] for k in ("revenue_minor", "fees_minor", "cost_minor", "contribution_minor")}
    expect = {
        "revenue_minor": o["total_minor"],
        "fees_minor": fee,
        "cost_minor": cost,
        "contribution_minor": o["total_minor"] - fee - cost,
    }
    step("finance_indicators", delta == expect, delta=delta, expected=expect)
    step("finance_says_not_profit", "não é lucro" in dash["note"].lower())
    # 16. histórico, auditoria e relatório
    hist = client.get(f"/v1/audit/events?subject_id={prop['id']}", headers=owner)
    actions = {e["action"] for e in hist.json().get("items", [])} if hist.status_code == 200 else set()
    step(
        "history_of_proposal",
        {"proposal.evaluated", "approval.approve"} <= actions,
        actions=sorted(actions),
    )
    with session_scope() as db:
        ok, _ = audit.verify_chain(db)
    step("audit_chain_intact", ok)
    t = client.post(
        "/v1/agent-tasks", headers=op, json={"agent_id": "orion", "kind": "analytics.report", "input": {}}
    ).json()
    drain()
    rep = client.get(f"/v1/agent-tasks/{t['id']}", headers=op).json()
    step("report_generated", rep["state"] == "COMPLETED", state=rep["state"])
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
