#!/usr/bin/env python3
"""Caos: queda do Postgres com o stack compose em DRY_RUN (QA-002, SDD §17 "falha banco").

Para o Postgres, verifica que a API continua viva (healthz 200), acusa indisponibilidade (readyz 503) e responde
503 DB_UNAVAILABLE (não 500); religa o banco e mede o tempo até readyz voltar; confirma que a API reconecta,
que o worker NÃO reiniciou (sobreviveu com backoff) e que uma tarefa nova é processada de ponta a ponta.
Uso (de apps/api): uv run python ../../scripts/chaos_db.py [--report ../../progress/test-reports/chaos_db.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra/compose/docker-compose.yml"), "--env-file",
           os.environ.get("AIC_CHAOS_ENV_FILE", str(ROOT / "infra/env/dry_run.env"))]
BASE = os.environ.get("AIC_CHAOS_BASE_URL", "http://localhost:8080/api")


def http(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def compose(*args) -> str:
    return subprocess.run([*COMPOSE, *args], check=True, capture_output=True, text=True).stdout


def identity(service: str) -> tuple:
    cid = compose("ps", "-q", service).strip()
    info = json.loads(subprocess.check_output(["docker", "inspect", cid], text=True))[0]
    return cid, info["State"]["StartedAt"], info["RestartCount"]


def worker_logs(since: str) -> str:
    result = subprocess.run([*COMPOSE, "logs", "--no-color", "--since", since, "worker"],
                            capture_output=True, text=True, check=True)
    return result.stdout + result.stderr


def wait(pred, timeout: float, step: float = 0.5) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return round(time.monotonic() - t0, 2)
        time.sleep(step)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(ROOT / "progress/test-reports/chaos_db.json"))
    ap.add_argument("--outage-s", type=float, default=8.0)
    ap.add_argument("--observe-timeout-s", type=float, default=60.0)
    a = ap.parse_args()
    # Refuse to stop any database unless the selected project explicitly runs simulation on loopback.
    config = json.loads(compose("config", "--format", "json"))
    if any(config["services"][service]["environment"].get("AIC_MODE") != "DRY_RUN"
           for service in ("api", "worker")) or not BASE.startswith(("http://localhost:", "http://127.0.0.1:")):
        raise SystemExit("Chaos requires explicit DRY_RUN and a loopback API")
    checks: dict = {}
    token = http("POST", "/v1/auth/login", {"email": "operator@demo.local", "password": "simulado-123"})[2][
        "access_token"]
    checks["baseline_ready"] = http("GET", "/readyz")[0] == 200
    worker_identity = identity("worker")
    api_identity = identity("api")
    since = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")

    compose("stop", "postgres")
    try:
        time.sleep(1)
        checks["healthz_200_during_outage"] = http("GET", "/healthz")[0] == 200
        checks["readyz_503_during_outage"] = http("GET", "/readyz")[0] == 503
        st, hdr, body = http("GET", "/v1/auth/me", token=token)
        checks["api_503_db_unavailable"] = st == 503 and body.get("detail", {}).get("code") == "DB_UNAVAILABLE"
        checks["api_retry_after"] = {k.lower(): v for k, v in hdr.items()}.get("retry-after") == "5"
        time.sleep(max(0, a.outage_s))
        # A fixed sleep does not prove that the worker observed the outage: a connection attempt
        # may still be pending. Hold the outage until an actual failed cycle, with a bounded timeout.
        checks["worker_failure_observed_s"] = wait(
            lambda: "ciclo do worker falhou" in worker_logs(since), a.observe_timeout_s)
    finally:
        t_start = time.monotonic()
        compose("start", "postgres")
    checks["recovery_readyz_s"] = wait(lambda: http("GET", "/readyz")[0] == 200, 60)
    checks["db_restart_to_ready_s"] = round(time.monotonic() - t_start, 2)
    checks["api_reconnected"] = wait(lambda: http("GET", "/v1/auth/me", token=token)[0] == 200, 30) is not None
    _, _, task = http("POST", "/v1/agent-tasks", {"agent_id": "luna", "kind": "research.mock",
                                                  "input": {"topic": "caos-banco"}}, token)
    done = wait(lambda: http("GET", f"/v1/agent-tasks/{task['id']}", token=token)[2].get("state") == "COMPLETED", 60)
    checks["task_after_recovery_completed_s"] = done
    checks["worker_recovery_observed_s"] = wait(
        lambda: "worker recuperado" in worker_logs(since), a.observe_timeout_s)
    checks["worker_not_restarted"] = identity("worker") == worker_identity
    checks["api_not_restarted"] = identity("api") == api_identity
    logs = worker_logs(since)
    checks["worker_logged_failures"] = "ciclo do worker falhou" in logs
    checks["worker_logged_recovery"] = "worker recuperado" in logs
    # 0.0 seconds is a valid observation, not False (Python equality conflates them).
    ok = all(v is not False and v is not None for v in checks.values())
    report = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "outage_s": a.outage_s,
              "ok": ok, "checks": checks}
    pathlib.Path(a.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
