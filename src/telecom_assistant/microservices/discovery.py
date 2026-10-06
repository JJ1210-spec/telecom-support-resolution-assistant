"""Independently deployable drift, discovery and taxonomy HTTP service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings
from ..services import Services, build_services
from .common import configure_internal_app


class PoolItem(BaseModel):
    ticket_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=5000)
    reason: str = Field(min_length=1)
    vector: list[float] | None = None


class DiscoveryRun(BaseModel):
    threshold: float = Field(default=0.72, ge=0, le=1)
    min_size: int = Field(default=3, ge=2, le=100)


class Decision(BaseModel):
    actor: str = Field(min_length=1)
    edits: dict | None = None


class DriftRun(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    persist: bool = True


class KBFlag(BaseModel):
    reason: str = Field(min_length=1)


class TaxonomyChange(BaseModel):
    actor: str = Field(min_length=1)
    action: Literal["add", "update", "deprecate", "merge"]
    data: dict


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

    app = FastAPI(title="Resolve Desk Discovery Service", version="1.0.0", lifespan=lifespan)
    app.state.services = services
    configure_internal_app(app, settings.internal_service_token)

    @app.get("/ready")
    async def ready() -> dict:
        if not await asyncio.to_thread(services.db.ping):
            raise HTTPException(503, "Discovery database unavailable")
        return {"status": "ready", "dependencies": {"database": "ok"}}

    @app.post("/api/v1/discovery/pool")
    async def add_to_pool(item: PoolItem) -> dict:
        await asyncio.to_thread(services.discovery.add, item.ticket_id, item.text, item.reason, item.vector)
        return {"added": True}

    @app.get("/api/v1/discovery/pool")
    async def pool() -> dict:
        return {"pool": await asyncio.to_thread(services.discovery.pool)}

    @app.post("/api/v1/discovery/run")
    async def run(req: DiscoveryRun) -> dict:
        return await services.discovery.run(req.threshold, req.min_size)

    @app.get("/api/v1/discovery/proposals")
    async def proposals(status: str | None = "pending") -> dict:
        return {"proposals": await asyncio.to_thread(services.discovery.proposals, status)}

    @app.post("/api/v1/discovery/proposals/{proposal_id}/{decision}")
    async def decide(proposal_id: str, decision: Literal["approve", "reject"], req: Decision) -> dict:
        try:
            return await asyncio.to_thread(services.discovery.decide, proposal_id, req.actor, decision, req.edits)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/taxonomy")
    async def taxonomy() -> dict:
        return {"version": services.registry.version(),
                "classes": await asyncio.to_thread(services.registry.classes, True),
                "history": await asyncio.to_thread(services.registry.history)}

    @app.post("/api/v1/taxonomy")
    async def taxonomy_change(req: TaxonomyChange) -> dict:
        try:
            version = await asyncio.to_thread(services.registry.apply_change, req.actor, req.action, req.data)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"version": version}

    @app.post("/api/v1/drift/run")
    async def drift_run(req: DriftRun) -> dict:
        return await asyncio.to_thread(services.drift.compute, req.days, req.persist)

    @app.get("/api/v1/drift/history")
    async def drift_history(limit: int = 30) -> dict:
        return {"history": await asyncio.to_thread(services.drift.history, limit)}

    @app.post("/api/v1/drift/flag-kb/{kb_id}")
    async def flag_kb(kb_id: str, req: KBFlag) -> dict:
        await asyncio.to_thread(services.drift.flag_kb, kb_id, req.reason)
        return {"kb_id": kb_id, "flagged": True}

    return app


def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
