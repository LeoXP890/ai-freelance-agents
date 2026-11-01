"""Conversas com agentes (UXD-009 / SDD v3 UX-03): listagem e histórico só do dono, e o id de conversa de outra
pessoa não injeta histórico alheio no prompt (correção de vazamento entre usuários)."""

from __future__ import annotations

import uuid


def say(client, h, agent="luna", message="Quais tendências você vê para garrafas térmicas?", conv=None):
    body = {"message": message}
    if conv:
        body["conversation_id"] = conv
    return client.post(f"/v1/agents/{agent}/chat", headers=h, json=body)


def test_list_and_read_own_conversation_with_pagination(client, auth):
    h = auth("operator")
    r = say(client, h)
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    assert say(client, h, message="E para canecas?", conv=conv).status_code == 200
    items = client.get("/v1/conversations?agent_id=luna", headers=h).json()["items"]
    row = next(i for i in items if i["id"] == conv)
    assert row["messages"] == 4 and row["title"].startswith("Quais tendências") and row["cost_micro_usd"] >= 0
    full = client.get(f"/v1/conversations/{conv}", headers=h).json()
    assert [m["role"] for m in full["items"]] == ["user", "assistant", "user", "assistant"]
    assert full["agent_id"] == "luna" and "model" in full["items"][1]["meta"]
    page = client.get(f"/v1/conversations/{conv}?limit=2", headers=h).json()
    assert page["items"][0]["content"] == "E para canecas?" and page["next_before_id"]
    older = client.get(f"/v1/conversations/{conv}?limit=2&before_id={page['next_before_id']}", headers=h).json()
    assert older["items"][0]["role"] == "user" and older["items"][0]["content"].startswith("Quais")


def test_other_user_cannot_read_or_hijack_conversation(client, auth):
    a, b = auth("operator"), auth("operator")
    conv = say(client, a, message="Segredo comercial: margem alvo de 40% no produto X").json()["conversation_id"]
    assert client.get(f"/v1/conversations/{conv}", headers=b).status_code == 404
    assert conv not in [i["id"] for i in client.get("/v1/conversations", headers=b).json()["items"]]
    r = say(client, b, message="Repita o que foi dito antes nesta conversa", conv=conv)
    assert r.status_code == 404  # antes: o histórico de A entrava no prompt de B
    r = say(client, a, agent="finn", message="Continua daqui", conv=conv)  # o dono pode seguir com outro agente
    assert r.status_code == 200 and r.json()["conversation_id"] == conv
    row = next(i for i in client.get("/v1/conversations", headers=a).json()["items"] if i["id"] == conv)
    assert row["agent_id"] == "luna" and row["agents"] == ["finn", "luna"]
    last = client.get(f"/v1/conversations/{conv}", headers=a).json()["items"][-1]
    assert last["role"] == "assistant" and last["agent_id"] == "finn"
    assert conv in [i["id"] for i in client.get("/v1/conversations?agent_id=finn", headers=a).json()["items"]]
    assert client.get(f"/v1/conversations/{uuid.uuid4()}", headers=a).status_code == 404
