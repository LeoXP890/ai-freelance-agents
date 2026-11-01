#!/usr/bin/env python3
"""Teste de carga leve (QA-004; SDD §5): mede, contra o stack compose via proxy, o p95 das leituras de painel
(SLO < 2 s sem consultas pesadas) e o atraso de propagação de eventos ao escritório por WebSocket (SLO <= 5 s).

Uso (de apps/api): uv run python ../../scripts/load_test.py [--users 20] [--requests 200]
Relatório: progress/test-reports/load_test.json. Mede; não presume (DoD do SDD).
"""

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import statistics
import time

import httpx
import websockets

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://localhost:8080/api"
READS = ["/v1/agent-presence/snapshot", "/v1/approvals?status=PENDING_APPROVAL", "/v1/finance/overview",
         "/v1/dashboard/sales?days=30", "/v1/orders", "/v1/alerts"]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def reads(token: str, users: int, total: int) -> dict:
    lat: dict[str, list[float]] = {r: [] for r in READS}
    errors = 0
    sem = asyncio.Semaphore(users)
    async with httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30) as c:
        async def one(i: int) -> None:
            nonlocal errors
            path = READS[i % len(READS)]
            async with sem:
                t0 = time.perf_counter()
                r = await c.get(path)
                lat[path].append(time.perf_counter() - t0)
                errors += r.status_code >= 400
        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(total)))
        wall = time.perf_counter() - t0
    allv = [x for v in lat.values() for x in v]
    return {"requests": total, "concurrency": users, "errors": errors, "throughput_rps": round(total / wall, 1),
            "p50_s": round(pct(allv, 50), 3), "p95_s": round(pct(allv, 95), 3), "max_s": round(max(allv), 3),
            "p95_by_route_s": {k: round(pct(v, 95), 3) for k, v in lat.items() if v}}


async def ws_latency(token: str, samples: int) -> dict:
    url = BASE.replace("http", "ws", 1) + "/v1/office/events"
    delays = []
    async with httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30) as c:
        cursor = (await c.get("/v1/agent-presence/snapshot")).json()["as_of_seq"]
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token, "after_seq": cursor}))
            assert json.loads(await ws.recv())["type"] == "ready"
            for _ in range(samples):
                t0 = time.perf_counter()
                tid = (await c.post("/v1/agent-tasks", json={"agent_id": "atlas", "kind": "foundation.heartbeat"})
                       ).json()["id"]
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if m.get("type") == "event" and m["event"]["task_id"] == tid:
                        delays.append(time.perf_counter() - t0)
                        break
    return {"samples": samples, "p50_s": round(pct(delays, 50), 3), "p95_s": round(pct(delays, 95), 3),
            "max_s": round(max(delays), 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--requests", type=int, default=300)
    ap.add_argument("--ws-samples", type=int, default=20)
    a = ap.parse_args()
    token = httpx.post(f"{BASE}/v1/auth/login", json={"email": "operator@demo.local", "password": "simulado-123"}
                       ).json()["access_token"]
    r = asyncio.run(reads(token, a.users, a.requests))
    w = asyncio.run(ws_latency(token, a.ws_samples))
    report = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "target": BASE, "reads": r,
              "office_events": w, "slo": {"panel_read_p95_lt_2s": r["p95_s"] < 2.0,
                                          "office_sync_p95_le_5s": w["p95_s"] <= 5.0, "no_errors": r["errors"] == 0},
              "note": "Máquina local de desenvolvimento; números de VPS precisam ser medidos lá (QA-006)."}
    report["ok"] = all(report["slo"].values())
    (ROOT / "progress/test-reports/load_test.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
