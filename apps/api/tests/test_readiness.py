"""Readiness diagnostics never replace a scoped go-live evaluation or a human authorization."""

from aicommerce.governance import golive


def test_readiness_without_scope_is_not_an_approval(client, auth):
    op = auth("operator")
    response = client.get("/v1/go-live/readiness", headers=op)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["diagnostic_only"] is True
    assert body["mode"] == "DRY_RUN"
    assert body["seller_type"] == "PERSON"
    assert not body["scope_complete"] and {"supplier_origin", "fulfillment_mode"} <= set(body["missing_scope"])
    assert body["live_operation_enabled_by_this_report"] is False
    assert {item["channel"] for item in body["channels"]} == {c.value for c in golive.Channel}
    assert all(item["policy_decision"] == "NOT_EVALUATED" for item in body["channels"])
    assert all(not item["commercial_activation_authorized"] for item in body["channels"])


def test_scoped_readiness_matches_policy_and_groups_actions(client, auth):
    op = auth("operator")
    params = {"uf": "SP", "supplier_origin": "NATIONAL", "fulfillment_mode": "DROPSHIP_SUPPLIER"}
    report = client.get("/v1/go-live/readiness", params=params, headers=op)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["scope_complete"] and not body["missing_scope"]
    assert body["scope"] == {**params, "product_id": None, "supplier_id": None}
    assert all(not row["commercial_activation_authorized"] for row in body["channels"])
    assert all(row["provider_account_verified"] is False for row in body["channels"])
    actions = {x["code"]: x for x in body["next_actions"]}
    assert "LEG-01" in actions and "TAX-01" in actions and "TAX-03" in actions
    assert "MERCADO_LIVRE" in actions["LEG-01"]["channels"]
    assert actions["LEG-01"]["reason_by_channel"]["MERCADO_LIVRE"]

    for row in body["channels"]:
        policy = client.post("/v1/go-live/evaluate", headers=op, json={**params, "channel": row["channel"]})
        assert policy.status_code == 200, policy.text
        expected = policy.json()
        assert row["policy_decision"] == expected["decision"] == "DENY"
        assert row["required_gates"] == expected["required_gates"]
        assert row["unmet"] == expected["unmet"]


def test_readiness_rejects_bad_scope_and_viewer(client, auth):
    op, viewer = auth("operator"), auth("viewer")
    assert client.get("/v1/go-live/readiness", headers=viewer).status_code == 403
    for bad in ("S", "SP1", "SÃO"):
        assert client.get("/v1/go-live/readiness", headers=op, params={"uf": bad}).status_code == 422
    invalid = client.get(
        "/v1/go-live/readiness",
        headers=op,
        params={"uf": "SP", "supplier_origin": "UNVERIFIED", "fulfillment_mode": "OWN_STOCK"},
    )
    assert invalid.status_code == 422
