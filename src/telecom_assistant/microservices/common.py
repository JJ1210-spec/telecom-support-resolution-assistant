"""Shared transport concerns for internal service APIs."""

from __future__ import annotations

import hmac
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..telemetry import configure_logging, log_event, metrics


def configure_internal_app(app: FastAPI, token: str) -> None:
    configure_logging()

    @app.middleware("http")
    async def internal_boundary(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        if request.url.path not in ("/health", "/ready", "/metrics") and token:
            supplied = request.headers.get("X-Service-Token", "")
            if not hmac.compare_digest(supplied, token):
                response = JSONResponse({"detail": "Invalid service token"}, status_code=403)
                response.headers["X-Request-ID"] = request_id
                return response
        try:
            response = await call_next(request)
        except Exception:
            metrics.inc("service_errors", service=app.title)
            log_event("service_request_error", service=app.title, request_id=request_id,
                      path=request.url.path)
            raise
        response.headers["X-Request-ID"] = request_id
        metrics.inc("service_requests", service=app.title, status=str(response.status_code // 100) + "xx")
        metrics.observe("service_latency", (time.perf_counter() - started) * 1000, service=app.title)
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    def prometheus() -> PlainTextResponse:
        return PlainTextResponse(metrics.prometheus())
