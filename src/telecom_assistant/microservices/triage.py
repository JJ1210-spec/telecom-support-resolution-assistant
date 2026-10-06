"""Independently deployable triage and adaptive-intake HTTP service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings
from ..knowledge.retrieval import knn_votes
from ..pii import redact_text
from ..services import Services, build_services
from .common import configure_internal_app


class TriageRequest(BaseModel):
    complaint: str = Field(min_length=5, max_length=5000)
    product_hint: str | None = None
    intake: dict | None = None
    classes: list[dict] | None = None
    knn: dict[str, float] | None = None
    neighbours: list[dict] | None = None
    taxonomy_version: int | None = None
    trace_id: str = ""


class TriageResponse(BaseModel):
    triage: dict
    meta: dict


class IntakeStart(BaseModel):
    complaint: str = Field(max_length=5000)
    area: str | None = None
    chosen_intent: str | None = None


class IntakeAnswer(BaseModel):
    state: dict
    question: dict
    option_ids: list[str] = Field(default_factory=list)
    text: str | None = None


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    services = services or build_services(settings, remote=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        services.registry.seed()
        await services.indexer.ensure()
        if services.index.backend == "local":
            await services.indexer.rebuild_from_db()
        yield

    app = FastAPI(title="Resolve Desk Triage Service", version="1.0.0", lifespan=lifespan)
    app.state.services = services
    configure_internal_app(app, settings.internal_service_token)

    @app.get("/ready")
    async def ready() -> dict:
        database = await asyncio.to_thread(services.db.ping)
        search = await services.index.ping()
        if not database or not search:
            raise HTTPException(503, "Triage dependencies unavailable")
        return {"status": "ready", "dependencies": {"database": "ok", "search": "ok"}}

    @app.post("/api/v1/triage", response_model=TriageResponse)
    async def triage(req: TriageRequest) -> TriageResponse:
        complaint = redact_text(req.complaint)
        neighbours = req.neighbours
        if neighbours is None:
            result = await services.retriever.search(complaint, top_tickets=10, top_kb=0)
            neighbours = result.tickets
        classes = req.classes if req.classes is not None else services.registry.classes()
        votes = req.knn if req.knn is not None else knn_votes(neighbours, k=10)
        result, meta = await services.triager.run(complaint, classes, votes, req.intake,
                                                  req.product_hint, req.trace_id,
                                                  req.taxonomy_version or services.registry.version(), neighbours)
        return TriageResponse(triage=result, meta=meta)

    @app.post("/api/v1/intake/start")
    async def intake_start(req: IntakeStart) -> dict:
        return await services.clarify.start(req.complaint, req.area, req.chosen_intent)

    @app.post("/api/v1/intake/answer")
    async def intake_answer(req: IntakeAnswer) -> dict:
        return await services.clarify.answer(req.state, req.question, req.option_ids, req.text)

    return app


def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
