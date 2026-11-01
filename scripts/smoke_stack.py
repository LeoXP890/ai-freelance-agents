#!/usr/bin/env python3
"""Smoke E2E contra o stack compose em DRY_RUN, passando pelo reverse proxy.

Verifica: health/readiness, login demo, snapshot com 13 agentes, tarefa criada → worker real processa →
COMPLETED, eventos via WebSocket com replay por offset, GoLive DENY e cabeçalho de modo.
Uso (de apps/api): uv run python ../../scripts/smoke_stack.py [--base http://localhost:8080/api] [--report path]
"""
import argparse
import asyncio
import datetime as dt
import json
import pathlib
import time
import urllib.request

import websockets


def http(base, method, path, body=None, token=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, {k.lower(): v for k, v in r.headers.items()}, json.loads(r.read() or b"{}")


async def ws_replay(base, token, after_seq, want_task):
    url = base.replace("http", "ws", 1) + "/v1/office/events"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token, "after_seq": after_seq}))
        assert json.loads(await ws.recv())["type"] == "ready"
        seen = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if msg["type"] == "event" and msg["event"]["task_id"] == want_task:
                seen.append(msg["event"])
                if msg["event"]["type"] == "agent.task.completed":
                    return seen
        raise AssertionError(f"evento de conclusão não recebido; vistos={len(seen)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080/api")
    ap.add_argument("--report", default=str(pathlib.Path(__file__).resolve().parents[1]
                                            / "progress/test-reports/smoke_stack.json"))
    a = ap.parse_args()
    checks = {}
    b = a.base
    checks["healthz"] = http(b, "GET", "/healthz")[2]["status"] == "ok"
    checks["readyz"] = http(b, "GET", "/readyz")[2]["database"] is True
    _, headers, info = http(b, "GET", "/v1/system/info")
    checks["mode_dry_run"] = info["mode"] == "DRY_RUN" and info["simulated"] and headers.get("x-aic-mode") == "DRY_RUN"
    token = http(b, "POST", "/v1/auth/login", {"email": "operator@demo.local", "password": "simulado-123"})[2][
        "access_token"]
    snap = http(b, "GET", "/v1/agent-presence/snapshot", token=token)[2]
    checks["snapshot_13_agents"] = len(snap["agents"]) == 13 and snap["simulated"] is True
    _, _, task = http(b, "POST", "/v1/agent-tasks", {"agent_id": "luna", "kind": "research.mock",
                                                     "input": {"topic": "smoke"}}, token)
    t0 = time.monotonic()
    evs = asyncio.run(ws_replay(b, token, snap["as_of_seq"], task["id"]))
    checks["ws_replay_and_stream"] = [e["state"] for e in evs] == ["PLANNING", "PLANNING", "WORKING", "TOOL_CALL",
                                                                   "COMPLETED"]
    checks["worker_completed_s"] = round(time.monotonic() - t0, 2)
    final = http(b, "GET", f"/v1/agent-tasks/{task['id']}", token=token)[2]
    checks["task_completed_simulated"] = final["state"] == "COMPLETED" and final["result"]["simulated"] is True
    gl = http(b, "POST", "/v1/go-live/evaluate", {"channel": "WOOCOMMERCE", "supplier_origin": "NATIONAL",
                                                  "fulfillment_mode": "DROPSHIP_SUPPLIER", "uf": "SP"}, token)[2]
    checks["golive_denied"] = gl["decision"] == "DENY"
    ok = all(v for k, v in checks.items() if k != "worker_completed_s")
    report = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "base": b, "ok": ok, "checks": checks}
    pathlib.Path(a.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
