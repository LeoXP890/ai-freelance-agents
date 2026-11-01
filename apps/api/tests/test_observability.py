import datetime as dt
import json
import logging

from aicommerce import observability
from aicommerce.db import session_scope
from aicommerce.models import SystemFlag

H = {"Authorization": "Bearer sim-metrics-token-dryrun"}


def test_metrics_require_token(client):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer errado"}).status_code == 401


def test_http_metrics_use_route_templates_not_ids(client, auth):
    h = auth("viewer")
    client.get("/v1/agent-tasks/00000000-0000-0000-0000-000000000000", headers=h)
    body = client.get("/metrics", headers=H).text
    assert 'route="/v1/agent-tasks/{task_id}"' in body
    assert "00000000-0000-0000-0000-000000000000" not in body
    assert "aic_http_request_duration_seconds_bucket" in body


def test_business_gauges_and_worker_heartbeat(client):
    observability.beat(force=True)
    body = client.get("/metrics", headers=H).text
    for name in (
        "aic_execution_jobs",
        "aic_agent_tasks",
        "aic_orders",
        "aic_alerts",
        "aic_webhooks_pending",
        "aic_ai_month_spend_micro_usd",
        "aic_emergency_stop_active",
        "aic_worker_heartbeat_age_seconds",
        "aic_execution_queue_oldest_seconds",
    ):
        assert name in body, name
    age = float(
        next(line.split()[-1] for line in body.splitlines() if line.startswith("aic_worker_heartbeat_age_seconds "))
    )
    assert age < 60


def test_stale_worker_raises_critical_alert(client, auth):
    with session_scope() as db:
        db.get(SystemFlag, observability.HEARTBEAT_KEY).updated_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    try:
        items = client.get("/v1/alerts", headers=auth("viewer")).json()["items"]
        assert any(a["kind"] == "WORKER_DOWN" and a["severity"] == "CRITICAL" for a in items)
    finally:
        observability.beat(force=True)


def test_json_log_format_carries_ids():
    rec = logging.LogRecord("aicommerce.worker", logging.INFO, __file__, 1, "tarefa %s", ("x",), None)
    rec.task_id = "t-1"
    out = json.loads(observability.JsonFormatter().format(rec))
    assert out["msg"] == "tarefa x" and out["task_id"] == "t-1" and out["level"] == "INFO"
