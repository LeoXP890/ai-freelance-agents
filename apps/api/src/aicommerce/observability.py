"""Observabilidade (INF-005; SDD §5): métricas Prometheus, heartbeat do worker e logs JSON estruturados.

* HTTP: contador e histograma por rota (template, não URL crua — evita cardinalidade explosiva e IDs em labels).
* Negócio/operação: coletados do banco no momento do scrape (fila de execução, tarefas, pedidos, alertas,
  custo de IA, kill switch, webhooks pendentes, idade do heartbeat do worker).
* `/metrics` exige token (AIC_METRICS_TOKEN): o proxy expõe /api/* publicamente.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select

REGISTRY = CollectorRegistry(auto_describe=True)
HTTP_REQUESTS = Counter("aic_http_requests_total", "Requisições HTTP", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "aic_http_request_duration_seconds",
    "Latência HTTP",
    ["route"],
    registry=REGISTRY,
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
HEARTBEAT_KEY = "worker_heartbeat"


class BusinessCollector:
    """Métricas de negócio lidas do banco. Banco fora não derruba o scrape: expõe aic_db_up 0 e segue."""

    def describe(self):
        # sem isto o prometheus_client chama collect() no registro, consultando o banco já na importação
        return []

    def collect(self):
        try:
            families = list(self._business())
        except Exception as e:
            logging.getLogger("aicommerce.metrics").error("métricas de negócio indisponíveis: %s", type(e).__name__)
            families = []
        yield GaugeMetricFamily("aic_db_up", "Banco acessível na coleta (1/0)", value=1 if families else 0)
        yield from families

    def _business(self):
        from aicommerce.ai.llm import month_spend_micro_usd
        from aicommerce.commerce import alerts
        from aicommerce.db import session_scope
        from aicommerce.governance import killswitch
        from aicommerce.models import AgentTask, ExecutionJob, Order, SystemFlag, WebhookEvent

        with session_scope() as db:
            g = GaugeMetricFamily("aic_execution_jobs", "Jobs de execução por estado", labels=["status"])
            for s, n in db.execute(select(ExecutionJob.status, func.count()).group_by(ExecutionJob.status)).all():
                g.add_metric([s], n)
            yield g
            oldest = db.execute(
                select(func.min(ExecutionJob.created_at)).where(ExecutionJob.status.in_(("QUEUED", "RETRY")))
            ).scalar()
            yield GaugeMetricFamily(
                "aic_execution_queue_oldest_seconds",
                "Idade do job pendente mais antigo",
                value=(dt.datetime.now(dt.UTC) - oldest).total_seconds() if oldest else 0,
            )
            g = GaugeMetricFamily("aic_agent_tasks", "Tarefas de agentes por estado", labels=["state"])
            for s, n in db.execute(select(AgentTask.state, func.count()).group_by(AgentTask.state)).all():
                g.add_metric([s], n)
            yield g
            g = GaugeMetricFamily("aic_orders", "Pedidos por estado", labels=["status"])
            for s, n in db.execute(select(Order.status, func.count()).group_by(Order.status)).all():
                g.add_metric([s], n)
            yield g
            a = alerts.compute(db)
            g = GaugeMetricFamily("aic_alerts", "Alertas operacionais", labels=["severity"])
            for sev in ("CRITICAL", "WARNING"):
                g.add_metric([sev], sum(x["severity"] == sev for x in a))
            yield g
            yield GaugeMetricFamily(
                "aic_webhooks_pending",
                "Webhooks aguardando processamento",
                value=db.execute(
                    select(func.count()).select_from(WebhookEvent).where(WebhookEvent.status == "RECEIVED")
                ).scalar_one(),
            )
            yield GaugeMetricFamily(
                "aic_ai_month_spend_micro_usd", "Gasto de IA no mês (µUS$)", value=month_spend_micro_usd(db)
            )
            yield GaugeMetricFamily(
                "aic_emergency_stop_active", "Kill switch ativo (1/0)", value=1 if killswitch.is_active(db) else 0
            )
            hb = db.get(SystemFlag, HEARTBEAT_KEY)
            age = (dt.datetime.now(dt.UTC) - hb.updated_at).total_seconds() if hb else float("inf")
            yield GaugeMetricFamily(
                "aic_worker_heartbeat_age_seconds", "Segundos desde o último ciclo do worker", value=age
            )


REGISTRY.register(BusinessCollector())


_last_beat = 0.0
BEAT_EVERY_S = 10.0


def beat(force: bool = False) -> None:
    """Chamado a cada ciclo do worker (gravado no máximo a cada 10 s); a idade vira métrica/alerta de worker parado."""
    global _last_beat
    from aicommerce.db import session_scope
    from aicommerce.models import SystemFlag

    if not force and time.monotonic() - _last_beat < BEAT_EVERY_S:
        return
    _last_beat = time.monotonic()
    now = dt.datetime.now(dt.UTC)
    with session_scope() as db:
        flag = db.get(SystemFlag, HEARTBEAT_KEY)
        if flag is None:
            db.add(
                SystemFlag(
                    key=HEARTBEAT_KEY, value={"pid": __import__("os").getpid()}, updated_by="worker", updated_at=now
                )
            )
        else:
            flag.updated_at = now


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("task_id", "job_id", "mission_id", "request_id", "kind"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0
