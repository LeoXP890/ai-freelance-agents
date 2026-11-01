#!/usr/bin/env python3
"""Verifica os alertas do Prometheus local contra falhas reais do stack (INF-005).

Para o Postgres → espera AicDatabaseUnavailable FIRING → religa → espera resolver.
Para o worker → espera AicWorkerDown FIRING (heartbeat > 120 s por 1 min) → religa → espera resolver.
Pré-requisito: stack DRY_RUN + scripts/observability.sh up. Relatório: progress/test-reports/prometheus_alerts.json
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra/compose/docker-compose.yml"), "--env-file",
           str(ROOT / "infra/env/dry_run.env")]


def firing(name: str) -> bool:
    with urllib.request.urlopen("http://localhost:9090/api/v1/alerts", timeout=5) as r:
        return any(a["labels"]["alertname"] == name and a["state"] == "firing"
                   for a in json.load(r)["data"]["alerts"])


def wait(pred, timeout: float) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return round(time.monotonic() - t0, 1)
        time.sleep(5)
    return None


def scenario(service: str, alert: str, fire_timeout: float) -> dict:
    subprocess.run([*COMPOSE, "stop", service], check=True, capture_output=True)
    try:
        fired = wait(lambda: firing(alert), fire_timeout)
    finally:
        subprocess.run([*COMPOSE, "start", service], check=True, capture_output=True)
    resolved = wait(lambda: not firing(alert), 240)
    return {"service_stopped": service, "alert": alert, "fired_after_s": fired, "resolved_after_restart_s": resolved}


def main() -> int:
    results = [scenario("postgres", "AicDatabaseUnavailable", 150), scenario("worker", "AicWorkerDown", 360)]
    ok = all(r["fired_after_s"] is not None and r["resolved_after_restart_s"] is not None for r in results)
    report = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "ok": ok,
              "prometheus": "v3.15.0", "scenarios": results}
    (ROOT / "progress/test-reports/prometheus_alerts.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
