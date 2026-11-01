def test_appearance_is_configurable_and_in_snapshot(client, auth):
    admin = auth("admin")
    r = client.put(
        "/v1/agents/luna/appearance",
        headers=admin,
        json={"color": "#22AA88", "accessory": "headset", "skin": "#c69c7a"},
    )
    assert r.status_code == 200 and r.json()["appearance"]["accessory"] == "headset"
    snap = client.get("/v1/agent-presence/snapshot", headers=admin).json()
    luna = next(a for a in snap["agents"] if a["agent_id"] == "luna")
    assert luna["color"] == "#22aa88" and luna["appearance"] == {"accessory": "headset", "skin": "#c69c7a"}


def test_appearance_validation_and_permission(client, auth):
    assert (
        client.put("/v1/agents/luna/appearance", headers=auth("operator"), json={"color": "#000000"}).status_code == 403
    )
    admin = auth("admin")
    assert client.put("/v1/agents/luna/appearance", headers=admin, json={"color": "vermelho"}).status_code == 422
    assert (
        client.put(
            "/v1/agents/luna/appearance", headers=admin, json={"color": "#000000", "accessory": "capa"}
        ).status_code
        == 422
    )
    assert client.put("/v1/agents/fantasma/appearance", headers=admin, json={"color": "#000000"}).status_code == 404


def test_seed_does_not_overwrite_user_appearance(client, auth):
    from aicommerce.db import session_scope
    from aicommerce.seed import seed_reference

    client.put("/v1/agents/vera/appearance", headers=auth("admin"), json={"color": "#123456", "accessory": "hat"})
    with session_scope() as db:
        seed_reference(db)
    vera = next(
        a
        for a in client.get("/v1/agent-presence/snapshot", headers=auth("viewer")).json()["agents"]
        if a["agent_id"] == "vera"
    )
    assert vera["color"] == "#123456" and vera["appearance"]["accessory"] == "hat"
