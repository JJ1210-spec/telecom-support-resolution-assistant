"""Independently deployable hybrid search and cited-resolution HTTP service."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..services import Services, build_services
from ..telemetry import log_event, metrics
from ..tickets.desk import SupportDesk
from .clients import RemoteTriager, ServiceUnavailable
from .common import configure_internal_app


class ResolveRequest(BaseModel):
    complaint: str = Field(min_length=5, max_length=5000)
    intake: dict | None = None
    product_hint: str | None = None
    trace_id: str = Field(min_length=1, max_length=80)


class ResolveResponse(BaseModel):
    trace_id: str
    triage: dict
    sources: list[dict]
    draft: dict | None
    decision: dict
    latency_ms: dict
    models: dict
    degraded: list[str]
    warnings: list[str]
    query_vector: list[float] | None
    top_similarity: float
    taxonomy_version: int


def create_app(settings: Settings | None = None, services: Services | None = None,
               triage_transport=None) -> FastAPI:
    settings = settings or Settings.from_env()
    services = services or build_services(settings, remote=False)
    if settings.triage_service_url:
        services.triager = RemoteTriager(settings.triage_service_url, settings.internal_service_token,
                                        transport=triage_transport)
    desk = SupportDesk(services)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        services.registry.seed()
        await services.indexer.ensure()
        if services.index.backend == "local":
            await services.indexer.rebuild_from_db()
        yield

    app = FastAPI(title="Resolve Desk Resolution Service", version="1.0.0", lifespan=lifespan)
    app.state.services, app.state.desk = services, desk
    configure_internal_app(app, settings.internal_service_token)

    @app.exception_handler(ServiceUnavailable)
    async def unavailable(_, exc: ServiceUnavailable):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/ready")
    async def ready() -> dict:
        database = await asyncio.to_thread(services.db.ping)
        vector = await services.index.ping()
        if not database or not vector:
            raise HTTPException(503, "Resolution dependencies unavailable")
        return {"status": "ready", "dependencies": {"database": "ok", "vector_store": "ok"}}

    @app.post("/api/v1/resolve", response_model=ResolveResponse)
    async def resolve(req: ResolveRequest) -> ResolveResponse:
        from ..pii import redact

        redacted, _ = redact(req.complaint)
        result = await desk.run_analysis(redacted, req.intake, req.product_hint, req.trace_id)
        return ResolveResponse.model_validate(result)

    @app.post("/api/v1/resolve/stream")
    async def resolve_stream(req: ResolveRequest) -> StreamingResponse:
        from ..pii import redact

        redacted, _ = redact(req.complaint)

        async def events():
            queue: asyncio.Queue[dict] = asyncio.Queue()

            async def analyze():
                try:
                    result = await desk.run_analysis(redacted, req.intake, req.product_hint, req.trace_id,
                                                     lambda stage: queue.put_nowait({"type": "progress", "stage": stage}))
                    await queue.put({"type": "result", "result": ResolveResponse.model_validate(result).model_dump()})
                except Exception as exc:  # noqa: BLE001 - stream headers are already sent
                    metrics.inc("service_errors", service=app.title)
                    log_event("resolution_stream_error", trace_id=req.trace_id, error=type(exc).__name__)
                    await queue.put({"type": "error", "detail": type(exc).__name__})

            task = asyncio.create_task(analyze())
            try:
                while True:
                    event = await queue.get()
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    if event["type"] in ("result", "error"):
                        break
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(events(), media_type="application/x-ndjson")

    return app


def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
