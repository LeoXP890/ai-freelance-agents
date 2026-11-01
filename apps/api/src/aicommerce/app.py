"""FastAPI — monólito modular (SDD §6). Uvicorn: aicommerce.app:app"""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
import uuid

from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from aicommerce import __version__
from aicommerce.api import (
    routes_ads,
    routes_ai,
    routes_approvals,
    routes_auth,
    routes_commerce,
    routes_desktop,
    routes_finance,
    routes_governance,
    routes_office,
    routes_orders,
    routes_users,
)
from aicommerce.config import get_settings
from aicommerce.db import get_engine
from aicommerce.observability import HTTP_LATENCY, HTTP_REQUESTS, REGISTRY

log = logging.getLogger("aicommerce.http")
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="AI Commerce API",
        version=__version__,
        description=f"Modo {s.mode.value} — {'SIMULADO: nenhuma operação real' if s.is_dry_run else 'LIVE'}",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        allow_credentials=False,
    )

    @app.exception_handler(sa_exc.OperationalError)
    @app.exception_handler(sa_exc.InterfaceError)
    async def db_unavailable(request: Request, exc: Exception):
        # Banco fora/conexão perdida: 503 explícito (o cliente re-tenta), sem vazar detalhes da conexão.
        logging.getLogger("aicommerce.api").error("banco indisponível: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": {"code": "DB_UNAVAILABLE", "message": "banco de dados indisponível; tente novamente"}},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _RID.match(incoming) else uuid.uuid4().hex
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "não_mapeada")  # template da rota, nunca a URL com IDs
        elapsed = time.perf_counter() - start
        HTTP_REQUESTS.labels(request.method, template, str(response.status_code)).inc()
        HTTP_LATENCY.labels(template).observe(elapsed)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-AIC-Mode"] = s.mode.value
        log.info(
            json.dumps(
                {
                    "rid": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                }
            )
        )
        return response

    @app.get("/metrics", tags=["system"], include_in_schema=False)
    def metrics(authorization: str | None = Header(default=None)):
        if not hmac.compare_digest(authorization or "", f"Bearer {s.metrics_token}"):
            return JSONResponse({"detail": "token de métricas inválido"}, status_code=401)
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz", tags=["system"])
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"])
    def readyz():
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable", "database": False}, status_code=503)
        return {"status": "ready", "database": True}

    @app.get("/v1/system/info", tags=["system"])
    def info():
        return {
            "version": __version__,
            "environment": s.environment.value,
            "mode": s.mode.value,
            "simulated": s.is_dry_run,
            "notice": "Ambiente simulado: resultados são fictícios e nenhuma integração real foi homologada."
            if s.is_dry_run
            else "LIVE: operações reais seguem bloqueadas por gates não aprovados.",
        }

    for r in (
        routes_auth,
        routes_governance,
        routes_approvals,
        routes_commerce,
        routes_orders,
        routes_ai,
        routes_finance,
        routes_office,
        routes_users,
        routes_ads,
        routes_desktop,
    ):
        app.include_router(r.router)
    return app


app = create_app()
