"""HTTP API (FastAPI): customer portal, agent console, admin, SSE stream, health/metrics, and the
React single-page app. In monolith mode the notification service is mounted at /notify."""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from typing import Literal

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import eval_runs, incidents, traces
from ..gateways.kv import rate_limited
from ..insights.stats import overview
from ..notify.service import create_notify_app
from ..services import Services, build_services
from ..telemetry import configure_logging, log_event, metrics, setup_otel
from ..tickets.desk import Forbidden, NotFound, SupportDesk
from ..tickets.lifecycle import HUMAN_QUEUE, OPEN, InvalidTransition
from .security import (
    COOKIE,
    SESSION_SECONDS,
    Accounts,
    admin_read,
    admin_write,
    agent_read,
    agent_write,
    current_user,
    customer_read,
    customer_write,
)


# ---------------------------------------------------------------------- request models
class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    name: str = Field(default="", max_length=120)
    region: str | None = Field(default=None, max_length=80)


class IntakeStart(BaseModel):
    complaint: str = Field(default="", max_length=5000)
    area: str | None = Field(default=None, max_length=60)
    intent: str | None = Field(default=None, max_length=100)


class IntakeAnswer(BaseModel):
    question_id: str = Field(max_length=60)
    option_ids: list[str] = Field(default_factory=list, max_length=10)
    text: str | None = Field(default=None, max_length=2000)


class TicketIn(BaseModel):
    complaint: str = Field(min_length=5, max_length=5000)
    product_hint: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=80)
    session_id: str | None = Field(default=None, max_length=40)


class StepFeedbackIn(BaseModel):
    status: Literal["worked", "did_not_work", "pending"]
    note: str | None = Field(default=None, max_length=1000)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    answered_option: str | None = Field(default=None, max_length=200)
    reply_to: str | None = Field(default=None, max_length=40)


class ConfirmIn(BaseModel):
    solved: bool
    note: str = Field(default="", max_length=2000)


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class AgentMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    options: list[str] = Field(default_factory=list, max_length=6)
    internal: bool = False
    request_info: bool = False


class ProposeIn(BaseModel):
    steps: list[str] = Field(min_length=1, max_length=10)
    message: str = Field(default="", max_length=4000)


class NoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=4000)


class AnalyzeIn(BaseModel):
    text: str = Field(min_length=5, max_length=5000)
    product_hint: str | None = None
    use_cache: bool = True


class KBDecisionIn(BaseModel):
    action: Literal["publish", "deprecate", "reject"]
    edits: dict | None = None


class KBCreateIn(BaseModel):
    kb_id: str = Field(min_length=3, max_length=60, pattern=r"^[A-Z0-9-]+$")
    title: str = Field(min_length=3, max_length=200)
    product: str | None = None
    intent: str | None = None
    summary: str = ""
    checks: list[str] = Field(default_factory=list)
    self_help: list[str] = Field(default_factory=list)
    escalation: str = ""


class TaxonomyIn(BaseModel):
    action: Literal["add", "update", "deprecate", "merge"]
    intent: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9_.]+$")
    label: str | None = None
    category: str | None = None
    product: str | None = None
    area: str | None = None
    description: str | None = None
    sensitive: bool | None = None
    into: str | None = None


class ProposalDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    edits: dict | None = None


class UserIn(Registration):
    role: Literal["customer", "agent", "admin"] = "agent"


def create_app(settings: Settings | None = None, services: Services | None = None,
               start_workers: bool = True) -> FastAPI:
    configure_logging()
    settings = settings or Settings.from_env()
    services = services or build_services(settings)
    desk = SupportDesk(services)
    accounts = Accounts(services.db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.registry.seed()
        await services.indexer.ensure()
        if services.index.backend == "local":
            rebuilt = await services.indexer.rebuild_from_db()
            log_event("local_index_rebuilt", **rebuilt)
        setup_otel(settings)
        workers = []
        if start_workers:
            workers = [asyncio.create_task(services.outbox.run()), asyncio.create_task(services.langfuse.run())]
        log_event("startup", **{k: v for k, v in settings.redacted().items() if k != "thresholds"})
        yield
        for task in workers:
            task.cancel()
        with contextlib.suppress(Exception):
            await services.langfuse.flush()

    app = FastAPI(title="Resolve Desk API", version="1.0.0", lifespan=lifespan,
                  description="Telecom support: adaptive intake, grounded self-service, human escalation, "
                              "learning knowledge base and drift monitoring.")
    app.state.services, app.state.desk, app.state.accounts, app.state.settings = services, desk, accounts, settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    if settings.deploy_mode == "monolith":
        app.mount("/notify", create_notify_app(settings, services.db))

    @app.exception_handler(NotFound)
    async def _nf(_: Request, exc: NotFound):
        return JSONResponse({"detail": f"Not found: {exc}"}, status_code=404)

    @app.exception_handler(Forbidden)
    async def _fb(_: Request, exc: Forbidden):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(InvalidTransition)
    async def _it(_: Request, exc: InvalidTransition):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _ve(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.middleware("http")
    async def _metrics(request: Request, call_next):
        import time

        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith(("/v1", "/auth")):
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            metrics.observe("http_latency", (time.perf_counter() - started) * 1000, path=path,
                            method=request.method)
            metrics.inc("http_requests", path=path, status=str(response.status_code // 100) + "xx")
        return response

    async def limit(request: Request, bucket: str, count: int, window: int) -> None:
        ip = request.client.host if request.client else "?"
        if await rate_limited(services.kv, f"{bucket}:{ip}", count, window):
            raise HTTPException(429, "Too many requests - please wait a moment")

    def set_session(response: Response, request: Request, user_id: str) -> str:
        token, csrf = accounts.new_session(user_id)
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
        return csrf

    # ------------------------------------------------------------------ system
    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    async def ready() -> dict:
        checks = {"database": await asyncio.to_thread(services.db.ping), "vector_store": await services.index.ping(),
                  "kv": await services.kv.ping()}
        if not checks["database"]:
            raise HTTPException(503, checks)
        return {"status": "ready", **checks}

    @app.get("/metrics", tags=["system"], response_class=PlainTextResponse)
    def prometheus() -> str:
        return metrics.prometheus()

    # ------------------------------------------------------------------ auth
    @app.post("/auth/register", tags=["auth"])
    async def register(data: Registration, request: Request, response: Response) -> dict:
        await limit(request, "register", 10, 3600)
        user = await asyncio.to_thread(accounts.create, data.email, data.password, "customer", data.name, data.region)
        csrf = await asyncio.to_thread(set_session, response, request, user["id"])
        return {**user, "csrf_token": csrf}

    @app.post("/auth/login", tags=["auth"])
    async def login(data: Credentials, request: Request, response: Response) -> dict:
        await limit(request, "login", 20, 600)
        user = await asyncio.to_thread(accounts.authenticate, data.email, data.password)
        if not user:
            raise HTTPException(401, "Invalid email or password (or the account is temporarily locked)")
        csrf = await asyncio.to_thread(set_session, response, request, user["id"])
        return {**user, "csrf_token": csrf}

    @app.get("/auth/me", tags=["auth"])
    def me(user: dict = Depends(current_user)) -> dict:
        return user

    @app.post("/auth/logout", tags=["auth"])
    def logout(request: Request, response: Response, user: dict = Depends(current_user)) -> dict:
        accounts.revoke(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/")
        return {"signed_out": True}

    # ------------------------------------------------------------------ catalog + intake (customer)
    @app.get("/v1/catalog", tags=["customer"])
    async def catalog(user: dict = Depends(current_user)) -> dict:
        classes = services.registry.classes()
        with services.db.read() as con:
            open_incidents = [dict(r._mapping) for r in con.execute(sa.select(
                incidents.c.id, incidents.c.title, incidents.c.region, incidents.c.intent, incidents.c.public_note)
                .where(incidents.c.status == "open"))]
        region = (user.get("region") or "").casefold()
        return {"areas": services.registry.areas(),
                "issues": [{"intent": c["intent"], "label": c["label"], "area": c["area"]} for c in classes],
                "known_incidents": [i for i in open_incidents if not region or (i["region"] or "").casefold() == region],
                "taxonomy_version": services.registry.version()}

    @app.post("/v1/intake/start", tags=["customer"])
    async def intake_start(data: IntakeStart, request: Request, user: dict = Depends(customer_write)) -> dict:
        await limit(request, "intake", 60, 600)
        return await desk.intake_start(user, data.complaint, data.area, data.intent)

    @app.post("/v1/intake/{session_id}/answer", tags=["customer"])
    async def intake_answer(session_id: str, data: IntakeAnswer, user: dict = Depends(customer_write)) -> dict:
        return await desk.intake_answer(user, session_id, data.question_id, data.option_ids, data.text)

    # ------------------------------------------------------------------ tickets (customer)
    @app.post("/v1/tickets", status_code=201, tags=["customer"])
    async def create_ticket(data: TicketIn, request: Request, user: dict = Depends(customer_write)) -> dict:
        await limit(request, "ticket", 20, 3600)
        return await desk.create_ticket(user, data.complaint, data.product_hint, data.region or user.get("region"),
                                        data.session_id)

    @app.get("/v1/tickets", tags=["customer"])
    async def my_tickets(user: dict = Depends(customer_read)) -> dict:
        rows = await asyncio.to_thread(desk.store.list, owner_id=user["id"])
        return {"tickets": [{k: r[k] for k in ("id", "subject", "status", "status_label", "route", "severity",
                                                "intent_label", "created_at", "updated_at", "analysis_state")}
                            for r in rows]}

    @app.get("/v1/tickets/{ticket_id}", tags=["customer"])
    async def my_ticket(ticket_id: str, user: dict = Depends(customer_read)) -> dict:
        return await desk.customer_ticket(user, ticket_id)

    @app.post("/v1/tickets/{ticket_id}/steps/{step_id}/feedback", tags=["customer"])
    async def step_feedback(ticket_id: str, step_id: str, data: StepFeedbackIn,
                            user: dict = Depends(customer_write)) -> dict:
        return await desk.step_feedback(user, ticket_id, step_id, data.status, data.note)

    @app.post("/v1/tickets/{ticket_id}/steps/{step_id}/chat", tags=["customer"])
    async def step_chat(ticket_id: str, step_id: str, data: TextIn, request: Request,
                        user: dict = Depends(customer_write)) -> dict:
        await limit(request, "stepchat", 40, 600)
        return await desk.step_chat(user, ticket_id, step_id, data.text)

    @app.post("/v1/tickets/{ticket_id}/messages", tags=["customer"])
    async def customer_message(ticket_id: str, data: MessageIn, user: dict = Depends(customer_write)) -> dict:
        return await desk.customer_message(user, ticket_id, data.body, data.answered_option, data.reply_to)

    @app.post("/v1/tickets/{ticket_id}/confirm", tags=["customer"])
    async def confirm(ticket_id: str, data: ConfirmIn, user: dict = Depends(customer_write)) -> dict:
        return await desk.customer_confirm(user, ticket_id, data.solved, data.note)

    @app.post("/v1/tickets/{ticket_id}/escalate", tags=["customer"])
    async def escalate(ticket_id: str, data: TextIn, user: dict = Depends(customer_write)) -> dict:
        return await desk.request_human(user, ticket_id, data.text)

    @app.post("/v1/tickets/{ticket_id}/feedback", tags=["customer"])
    async def ticket_feedback(ticket_id: str, data: FeedbackIn, user: dict = Depends(customer_write)) -> dict:
        return await desk.feedback(user, ticket_id, data.rating, data.comment)

    # ------------------------------------------------------------------ live events (SSE)
    @app.get("/v1/events", tags=["realtime"])
    async def events(request: Request, user: dict = Depends(current_user)) -> StreamingResponse:
        channels = [f"user:{user['id']}"] + (["agents"] if user["role"] in ("agent", "admin") else [])
        queues = [(c, desk.bus.subscribe(c)) for c in channels]

        async def stream():
            try:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    getters = [asyncio.create_task(q.get()) for _, q in queues]
                    done, pending = await asyncio.wait(getters, timeout=15, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    if not done:
                        yield ": keepalive\n\n"
                    for task in done:
                        yield f"data: {task.result()}\n\n"
            finally:
                for channel, queue in queues:
                    desk.bus.unsubscribe(channel, queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ agent console
    @app.get("/v1/agent/queue", tags=["agent"])
    async def queue(scope: Literal["human", "open", "all", "mine"] = "human", user: dict = Depends(agent_read)) -> dict:
        statuses = {"human": HUMAN_QUEUE, "open": OPEN, "all": None, "mine": None}[scope]
        rows = await asyncio.to_thread(desk.store.list, statuses=statuses,
                                       assignee_id=user["id"] if scope == "mine" else None)
        order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
        if scope in ("human", "mine"):
            rows.sort(key=lambda r: (order.get(r.get("severity") or "P3", 2), r.get("sla_hours_left") or 1e9))
        return {"tickets": rows, "counts": await asyncio.to_thread(desk.queue_counts)}

    @app.get("/v1/agent/tickets/{ticket_id}", tags=["agent"])
    async def agent_ticket(ticket_id: str, user: dict = Depends(agent_read)) -> dict:
        return await desk.agent_ticket(ticket_id)

    @app.post("/v1/agent/tickets/{ticket_id}/claim", tags=["agent"])
    async def claim(ticket_id: str, user: dict = Depends(agent_write)) -> dict:
        return await desk.claim(user, ticket_id)

    @app.post("/v1/agent/tickets/{ticket_id}/messages", tags=["agent"])
    async def agent_message(ticket_id: str, data: AgentMessageIn, user: dict = Depends(agent_write)) -> dict:
        return await desk.agent_message(user, ticket_id, data.body, data.options, data.internal, data.request_info)

    @app.post("/v1/agent/tickets/{ticket_id}/propose", tags=["agent"])
    async def propose(ticket_id: str, data: ProposeIn, user: dict = Depends(agent_write)) -> dict:
        return await desk.propose_solution(user, ticket_id, data.steps, data.message)

    @app.post("/v1/agent/tickets/{ticket_id}/resolve", tags=["agent"])
    async def agent_resolve(ticket_id: str, data: NoteIn, user: dict = Depends(agent_write)) -> dict:
        return await desk.agent_resolve(user, ticket_id, data.note)

    @app.post("/v1/agent/tickets/{ticket_id}/copilot", tags=["agent"])
    async def copilot(ticket_id: str, user: dict = Depends(agent_write)) -> dict:
        return await desk.refresh_copilot(ticket_id)

    @app.get("/v1/agent/tickets/{ticket_id}/emails", tags=["agent"])
    async def ticket_emails(ticket_id: str, user: dict = Depends(agent_read)) -> dict:
        return {"emails": await asyncio.to_thread(services.notifications.list, ticket_id)}

    @app.post("/v1/agent/analyze", tags=["agent"])
    async def playground(data: AnalyzeIn, request: Request, user: dict = Depends(agent_write)) -> dict:
        await limit(request, "playground", 30, 600)
        return await desk.analyze_text(data.text, None, data.product_hint, data.use_cache)

    @app.get("/v1/agent/traces/{trace_id}", tags=["agent"])
    def trace(trace_id: str, user: dict = Depends(agent_read)) -> dict:
        with services.db.read() as con:
            row = con.execute(sa.select(traces).where(traces.c.trace_id == trace_id)).first()
        if not row:
            raise HTTPException(404, "Trace not found")
        return {**dict(row._mapping), "created_at": row.created_at.isoformat()}

    @app.get("/v1/agent/incidents", tags=["agent"])
    async def list_incidents(user: dict = Depends(agent_read)) -> dict:
        return {"incidents": await asyncio.to_thread(services.incidents.list)}

    @app.post("/v1/agent/incidents/{incident_id}/resolve", tags=["agent"])
    async def resolve_incident(incident_id: str, data: NoteIn, user: dict = Depends(agent_write)) -> dict:
        return await desk.resolve_incident(user, incident_id, data.note)

    @app.get("/v1/agent/stats", tags=["agent"])
    async def stats(user: dict = Depends(agent_read)) -> dict:
        return await asyncio.to_thread(overview, services.db)

    # ------------------------------------------------------------------ admin: knowledge, taxonomy, drift
    @app.get("/v1/admin/kb", tags=["admin"])
    async def kb(status: str | None = None, user: dict = Depends(agent_read)) -> dict:
        return {"articles": await asyncio.to_thread(desk.kb_list, status)}

    @app.post("/v1/admin/kb", tags=["admin"])
    async def kb_create(data: KBCreateIn, user: dict = Depends(admin_write)) -> dict:
        result = await services.indexer.upsert_kb({**data.model_dump(), "version": 1, "status": "published",
                                                   "origin": "manual"}, actor=user["id"])
        return {"kb_id": data.kb_id, "index": result}

    @app.post("/v1/admin/kb/{kb_id}/decision", tags=["admin"])
    async def kb_decision(kb_id: str, data: KBDecisionIn, user: dict = Depends(admin_write)) -> dict:
        return await desk.kb_decide(user, kb_id, data.action, data.edits)

    @app.get("/v1/admin/taxonomy", tags=["admin"])
    async def taxonomy(user: dict = Depends(agent_read)) -> dict:
        return {"version": services.registry.version(),
                "classes": await asyncio.to_thread(services.registry.classes, True),
                "history": await asyncio.to_thread(services.registry.history)}

    @app.post("/v1/admin/taxonomy", tags=["admin"])
    async def taxonomy_change(data: TaxonomyIn, user: dict = Depends(admin_write)) -> dict:
        payload = {k: v for k, v in data.model_dump().items() if v is not None and k != "action"}
        version = await asyncio.to_thread(services.registry.apply_change, user["id"], data.action, payload)
        return {"version": version}

    @app.get("/v1/admin/discovery", tags=["admin"])
    async def discovery(user: dict = Depends(agent_read)) -> dict:
        return {"pool": await asyncio.to_thread(services.discovery.pool),
                "proposals": await asyncio.to_thread(services.discovery.proposals, None)}

    @app.post("/v1/admin/discovery/run", tags=["admin"])
    async def discovery_run(user: dict = Depends(admin_write)) -> dict:
        return await services.discovery.run()

    @app.post("/v1/admin/discovery/{proposal_id}/decision", tags=["admin"])
    async def proposal_decision(proposal_id: str, data: ProposalDecisionIn, user: dict = Depends(admin_write)) -> dict:
        return await asyncio.to_thread(services.discovery.decide, proposal_id, user["id"], data.decision, data.edits)

    @app.get("/v1/admin/drift", tags=["admin"])
    async def drift(days: int = 7, user: dict = Depends(agent_read)) -> dict:
        current = await asyncio.to_thread(services.drift.compute, days, True)
        return {**current, "history": await asyncio.to_thread(services.drift.history)}

    @app.post("/v1/admin/drift/flag-kb/{kb_id}", tags=["admin"])
    async def flag_kb(kb_id: str, data: NoteIn, user: dict = Depends(admin_write)) -> dict:
        await asyncio.to_thread(services.drift.flag_kb, kb_id, data.note)
        return {"kb_id": kb_id, "flagged": True}

    @app.get("/v1/admin/health", tags=["admin"])
    async def system_health(user: dict = Depends(agent_read)) -> dict:
        return {
            "components": {"database": await asyncio.to_thread(services.db.ping),
                           "vector_store": {"backend": services.index.backend, "ok": await services.index.ping()},
                           "kv": {"backend": services.kv.backend, "ok": await services.kv.ping()},
                           "llm": services.llm is not None, "reranker": bool(services.reranker)},
            "providers": await services.llm.quota_report() if services.llm else [],
            "outbox": await asyncio.to_thread(services.outbox.stats),
            "metrics": metrics.snapshot(), "config": settings.redacted(),
            "index_counts": {"tickets": await services.index.count("tickets"), "kb": await services.index.count("kb")},
        }

    @app.post("/v1/admin/outbox/replay", tags=["admin"])
    async def replay(user: dict = Depends(admin_write)) -> dict:
        return {"replayed": await asyncio.to_thread(services.outbox.replay, None)}

    @app.get("/v1/admin/emails", tags=["admin"])
    async def emails(user: dict = Depends(agent_read)) -> dict:
        return {"emails": await asyncio.to_thread(services.notifications.list, None, 100)}

    @app.get("/v1/admin/evals", tags=["admin"])
    def evals(user: dict = Depends(agent_read)) -> dict:
        with services.db.read() as con:
            rows = con.execute(sa.select(eval_runs).order_by(eval_runs.c.created_at.desc()).limit(20)).all()
        return {"runs": [{**dict(r._mapping), "created_at": r.created_at.isoformat()} for r in rows]}

    @app.get("/v1/admin/users", tags=["admin"])
    async def list_users(user: dict = Depends(admin_read)) -> dict:
        return {"users": await asyncio.to_thread(accounts.list, None)}

    @app.post("/v1/admin/users", tags=["admin"])
    async def create_user(data: UserIn, user: dict = Depends(admin_write)) -> dict:
        return await asyncio.to_thread(accounts.create, data.email, data.password, data.role, data.name, data.region)

    # ------------------------------------------------------------------ React SPA
    dist = settings.frontend_dist
    if dist.exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            if path.startswith(("v1/", "auth/", "notify/")):
                raise HTTPException(404, "Not found")
            return FileResponse(dist / "index.html")

    app.state.json = json
    return app
