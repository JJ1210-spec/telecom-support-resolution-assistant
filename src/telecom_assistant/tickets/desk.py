"""SupportDesk: orchestrates intake -> analysis -> routing -> self-service / human loop -> resolution -> learning.

Flow for a new ticket
1. Intake: adaptive clarifying questions (ClarifyEngine) narrow the candidate issues before submission.
2. The ticket row, its creation event and an acknowledgement email are written in ONE transaction
   (transactional outbox). The email is held ~20 s so analysis can enrich it with the outcome, but it is
   sent regardless if analysis crashes.
3. Analysis (background): redact -> hybrid retrieval -> k-NN votes -> LLM triage -> grounded draft ->
   citation validation -> routing decision -> incident radar -> discovery pool -> trace.
4. self_service / assisted: customer gets a checklist of grounded steps with "worked / didn't work" and a
   per-step chat. All steps failing (or asking for a human) escalates the same ticket with the attempt log.
5. human: admins own the fix and get a copilot brief. If a reviewed public KB supports it, the customer
   also gets up to two read-only precautions while waiting, never an admin diagnostic action.
6. Admin replies / asks for info with quick-reply options / proposes a solution; the customer confirms or
   reopens. The ticket stays live until confirmed.
7. Resolved -> outbox "learn": the whole process is summarized, indexed as a new searchable case, and a KB
   article is proposed when the fix is novel. Reopening later down-weights that learned case.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from ..ai.clarify import ClarifyEngine
from ..ai.resolver import route, select_precaution_steps
from ..db import corpus_tickets, kb_articles, tickets, traces, users, utc_now, vec_to_bytes
from ..knowledge.retrieval import knn_votes
from ..notify.outbox import enqueue, refresh_pending
from ..pii import redact
from ..services import Services, as_vector
from ..telemetry import Timer, log_event, metrics
from .events import EventBus
from .lifecycle import HUMAN_QUEUE, OPEN, InvalidTransition
from .store import TicketStore


class NotFound(LookupError):
    pass


class Forbidden(PermissionError):
    pass


CUSTOMER_REASON = {
    "self_service": "We've solved this exact issue {n} times before, so here are steps you can try right now.",
    "assisted": "An admin is reviewing your ticket. Meanwhile, these safe checks may fix it faster.",
    "human": "This needs an admin, so we've sent it straight to our support team.",
}
HUMAN_PRECAUTION_REASON = ("An admin needs to investigate and handle the fix. "
                           "While they review your ticket, these safe checks can help without changing your service.")


class SupportDesk:
    def __init__(self, services: Services, bus: EventBus | None = None) -> None:
        self.s = services
        self.store = TicketStore(services.db)
        self.bus = bus or EventBus()
        self.tasks: set[asyncio.Task] = set()
        services.outbox.register("learn", self._learn_handler)

    # ------------------------------------------------------------------ utils
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _user(self, user_id: str) -> dict:
        with self.s.db.read() as con:
            row = con.execute(sa.select(users.c.id, users.c.email, users.c.name).where(users.c.id == user_id)).first()
        return dict(row._mapping) if row else {"email": "", "name": ""}

    def _email(self, con, ticket: dict, template: str, **context) -> str:
        owner = self._user(ticket["owner_id"])
        triage = ticket.get("triage") or {}
        payload = {"template": template, "to": owner["email"], "ticket_id": ticket["id"], "context": {
            "name": owner.get("name") or owner["email"].split("@")[0], "url": f"{self.s.settings.app_url}/tickets/"
            f"{ticket['id']}", "issue": triage.get("intent_label") or ticket.get("subject"),
            "severity": triage.get("severity") or "-", **context}}
        return enqueue(con, "email", payload)

    async def _own(self, ticket_id: str, user: dict) -> dict:
        ticket = await asyncio.to_thread(self.store.full, ticket_id)
        if not ticket:
            raise NotFound(ticket_id)
        if user["role"] == "customer" and ticket["owner_id"] != user["id"]:
            raise NotFound(ticket_id)
        return ticket

    def _notify(self, ticket: dict, kind: str, **data) -> None:
        self.bus.ticket(ticket["id"], ticket["owner_id"], kind, **data)
        self.s.outbox.kick()

    # ------------------------------------------------------------------ intake
    async def intake_start(self, user: dict, complaint: str, area: str | None, intent: str | None) -> dict:
        state = await self.s.clarify.start(complaint, area, intent)
        session_id = await asyncio.to_thread(self.store.save_intake, user["id"], state)
        metrics.inc("intake_started")
        return {"session_id": session_id, **state}

    async def intake_answer(self, user: dict, session_id: str, question_id: str, option_ids: list[str],
                            text: str | None) -> dict:
        row = await asyncio.to_thread(self.store.get_intake, user["id"], session_id)
        if not row:
            raise NotFound(session_id)
        state = row["state"]
        question = state.get("next_question")
        if not question or question["id"] != question_id:
            raise ValueError("That question is no longer pending")
        state = await self.s.clarify.answer(state, question, option_ids, text)
        await asyncio.to_thread(self.store.save_intake, user["id"], state, session_id)
        metrics.inc("intake_answers", kind=question.get("kind", "?"))
        return {"session_id": session_id, **state}

    # ------------------------------------------------------------------ create + analyze
    async def create_ticket(self, user: dict, complaint: str, product_hint: str | None, region: str | None,
                            session_id: str | None) -> dict:
        intake = None
        if session_id:
            row = await asyncio.to_thread(self.store.get_intake, user["id"], session_id)
            intake = row["state"] if row else None
        text = ClarifyEngine.enriched_complaint(intake) if intake else complaint
        redacted, pii = redact(text)
        subject = complaint.strip().split("\n")[0][:120]

        def _create() -> tuple[str, str]:
            with self.s.db.tx() as con:
                ticket_id = self.store.create(con, user["id"], complaint, redacted, product_hint, region, intake,
                                              subject)
                if session_id:
                    self.store.link_intake(con, session_id, ticket_id)
                owner = self._user(user["id"])
                ack = enqueue(con, "email", {"template": "ticket_received", "to": owner["email"],
                                             "ticket_id": ticket_id, "context": {
                                                 "name": owner.get("name"), "status_label": "Received",
                                                 "url": f"{self.s.settings.app_url}/tickets/{ticket_id}"}},
                              delay_s=20)
                if pii:
                    self.store.event(con, ticket_id, "system", "system", "pii_redacted", {"types": pii})
                return ticket_id, ack

        ticket_id, ack_id = await asyncio.to_thread(_create)
        metrics.inc("tickets_created")
        self._spawn(self.analyze_ticket(ticket_id, ack_id))
        ticket = await asyncio.to_thread(self.store.full, ticket_id)
        self.bus.ticket(ticket_id, user["id"], "created")
        return self.store.customer_view(ticket)

    async def run_analysis(self, redacted: str, intake: dict | None, product_hint: str | None, trace_id: str,
                           progress=None) -> dict:
        """Stateless analysis used by tickets, the admin playground and the eval harness."""
        timer = Timer()
        degraded: list[str] = []
        progress = progress or (lambda stage: None)
        progress("retrieving")
        retrieval = await self.s.retriever.search(redacted, top_tickets=10, top_kb=4)
        timer.mark("retrieval")
        degraded += retrieval.degraded
        classes = self.s.registry.classes()
        knn = knn_votes(retrieval.tickets, k=10)
        progress("triaging")
        triage, triage_meta = await self.s.triager.run(redacted, classes, knn, intake, product_hint, trace_id,
                                                       self.s.registry.version(), retrieval.tickets)
        timer.mark("triage")
        degraded += triage_meta.get("degraded", [])
        progress("drafting")
        sources = await self.s.resolver.evidence(retrieval, triage)
        tried = list((triage.get("entities") or {}).get("actions_tried") or [])
        draft, draft_meta = await self.s.resolver.draft(redacted, triage, sources, trace_id, tried)
        timer.mark("draft")
        degraded += draft_meta.get("degraded", [])
        decision = route(self.s.settings, triage, retrieval, draft, self.s.registry.by_intent(), intake)
        decision["customer_reason"] = CUSTOMER_REASON[decision["route"]].format(n=decision["recurrence"])
        if (decision["route"] == "human" and draft and triage.get("intent") != "other"
                and not triage.get("prompt_injection") and triage.get("confidence", 0) >= 0.5
                and retrieval.top_similarity >= self.s.settings.min_retrieval_score
                and not (intake or {}).get("chose_other")):
            draft["precaution_steps"] = select_precaution_steps(draft, sources, triage.get("intent"))
            if draft["precaution_steps"]:
                decision["customer_reason"] = HUMAN_PRECAUTION_REASON
        cited_ids = {citation for step in (draft or {}).get("customer_steps", []) +
                     (draft or {}).get("admin_steps", []) + (draft or {}).get("precaution_steps", [])
                     for citation in step.get("citations", [])}
        all_sources = list(dict((s["id"], s) for s in sources + retrieval.tickets).values())
        compact_sources = [{k: s.get(k) for k in ("id", "kind", "title", "snippet", "similarity", "rerank", "rrf",
                                                  "intent", "product", "audience", "root_cause", "steps", "origin",
                                                  "outcome_score", "resolved_at", "section", "kb_id")}
                           for i, s in enumerate(all_sources) if i < 16 or s["id"] in cited_ids]
        latency = {**timer.marks, "total": timer.total(), "retrieval_detail": retrieval.latency_ms}
        models = {"triage": triage_meta.get("model"), "draft": draft_meta.get("model"),
                  "embed": getattr(self.s.embedder, "model", None),
                  "rerank": self.s.reranker.model if self.s.reranker else None,
                  "prompts": {"triage": triage_meta.get("prompt_version"), "draft": draft_meta.get("prompt_version")},
                  "fallback_path": {"triage": triage_meta.get("fallback_path"),
                                    "draft": draft_meta.get("fallback_path")}}
        metrics.observe("analysis_latency", latency["total"])
        metrics.inc("routing", route=decision["route"])
        return {"trace_id": trace_id, "triage": triage, "sources": compact_sources, "draft": draft,
                "decision": decision, "latency_ms": latency, "models": models, "degraded": sorted(set(degraded)),
                "warnings": draft_meta.get("warnings", []) + triage.get("notes", []),
                "query_vector": retrieval.query_vector, "top_similarity": retrieval.top_similarity,
                "taxonomy_version": self.s.registry.version()}

    async def analyze_text(self, text: str, intake: dict | None = None, product_hint: str | None = None,
                           use_cache: bool = True) -> dict:
        redacted, _ = redact(text)
        key = "resolve:" + hashlib.sha256(json.dumps([redacted, product_hint, self.s.registry.version(),
                                                      (intake or {}).get("answers")], default=str).encode()).hexdigest()
        if use_cache:
            cached = await self.s.kv.get(key)
            if cached:
                metrics.inc("resolve_cache", outcome="hit")
                return {**cached, "cache": "hit"}
        trace_id = f"tr_{uuid.uuid4().hex[:16]}"
        result = await self.run_analysis(redacted, intake, product_hint, trace_id)
        result.pop("query_vector", None)
        await self._save_trace(trace_id, None, "playground", result)
        if use_cache and not result["degraded"]:
            await self.s.kv.set(key, result, ttl=self.s.settings.cache_ttl_s)
        metrics.inc("resolve_cache", outcome="miss")
        return {**result, "cache": "miss"}

    async def _save_trace(self, trace_id: str, ticket_id: str | None, kind: str, result: dict) -> None:
        data = {k: result.get(k) for k in ("triage", "decision", "draft", "warnings", "taxonomy_version")}
        data["sources"] = [{k: s.get(k) for k in ("id", "kind", "similarity", "rerank", "rrf")}
                           for s in result.get("sources", [])]

        def _write() -> None:
            with self.s.db.tx() as con:
                con.execute(traces.insert().values(trace_id=trace_id, ticket_id=ticket_id, kind=kind, data=data,
                                                   latency_ms=result.get("latency_ms", {}),
                                                   models=result.get("models", {}),
                                                   degraded=result.get("degraded", []), created_at=utc_now()))

        await asyncio.to_thread(_write)
        self.s.langfuse.trace(trace_id, f"ticket.{kind}", input={"ticket_id": ticket_id},
                              output={"route": (result.get("decision") or {}).get("route"),
                                      "intent": (result.get("triage") or {}).get("intent")},
                              latency_ms=result.get("latency_ms", {}).get("total"), degraded=result.get("degraded"))

    async def analyze_ticket(self, ticket_id: str, ack_id: str | None = None) -> None:
        ticket = await asyncio.to_thread(self.store.raw, ticket_id)
        if not ticket:
            return
        trace_id = f"tr_{uuid.uuid4().hex[:16]}"

        def progress(stage: str) -> None:
            self.bus.ticket(ticket_id, ticket["owner_id"], "analysis_progress", stage=stage)

        try:
            result = await self.run_analysis(ticket["complaint_redacted"], ticket.get("intake"),
                                             ticket.get("product_hint"), trace_id, progress)
        except Exception as exc:  # noqa: BLE001 - a ticket must never be lost because analysis failed
            log_event("analysis_failed", ticket_id=ticket_id, error=f"{type(exc).__name__}: {exc}")
            metrics.inc("analysis_failed")

            def _fail() -> None:
                with self.s.db.tx() as con:
                    self.store.transition(con, ticket_id, "escalated", "system", "system",
                                          {"reason": "Automatic analysis failed; sent to an admin"},
                                          analysis_state="failed", route="human",
                                          sla_due_at=self.store.sla_due("P2"))
                    self.store.add_message(con, ticket_id, "system", "system",
                                           "We've received your request and an admin will take it from here.")

            await asyncio.to_thread(_fail)
            self._notify(ticket, "analyzed")
            return
        await self._apply_analysis(ticket, result, ack_id)

    async def _apply_analysis(self, ticket: dict, result: dict, ack_id: str | None) -> None:
        ticket_id = ticket["id"]
        triage, decision, draft = result["triage"], result["decision"], result["draft"] or {}
        vector = as_vector(result.get("query_vector"))
        route_name = decision["route"]
        customer_steps = (draft.get("precaution_steps", []) if route_name == "human"
                          else draft.get("customer_steps", []))
        admin_steps = draft.get("admin_steps", [])
        status = "self_service" if route_name == "self_service" else "escalated"
        created_at = datetime.fromisoformat(ticket["created_at"]) if ticket.get("created_at") else None

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.update(con, ticket_id, triage=triage, sources=result["sources"],
                                  decision={**decision, "draft_meta": {
                                      "abstain": draft.get("abstain"), "abstain_reason": draft.get("abstain_reason"),
                                      "probable_root_cause": draft.get("probable_root_cause"),
                                      "escalate_if": draft.get("escalate_if"),
                                      "citation_coverage": draft.get("citation_coverage"),
                                      "citation_validity": draft.get("citation_validity"),
                                      "confidence": draft.get("confidence")},
                                      "warnings": result["warnings"], "degraded": result["degraded"],
                                      "latency_ms": result["latency_ms"], "models": result["models"]},
                                  intent=triage["intent"], category=triage.get("category"),
                                  product=triage.get("product"), severity=triage["severity"],
                                  sentiment=triage.get("sentiment"), confidence=triage.get("confidence"),
                                  language=triage.get("language"), route=route_name, trace_id=result["trace_id"],
                                  embedding=vec_to_bytes(vector) if vector else None,
                                  top_similarity=result["top_similarity"], analysis_state="done",
                                  sla_due_at=self.store.sla_due(triage["severity"], created_at))
                if customer_steps:
                    self.store.replace_steps(con, ticket_id, "ai", customer_steps, True, 1)
                if admin_steps:
                    self.store.replace_steps(con, ticket_id, "ai", admin_steps, False, 1)
                self.store.transition(con, ticket_id, status, "system", "system",
                                      {"route": route_name, "reasons": decision["reasons"]})
                self.store.event(con, ticket_id, "system", "system", "analyzed",
                                 {"route": route_name, "intent": triage.get("intent_label"),
                                  "severity": triage["severity"], "trace_id": result["trace_id"]})
                body = decision["customer_reason"]
                if customer_steps and route_name != "human" and draft.get("customer_message"):
                    body = f"{body}\n\n{draft['customer_message']}"
                self.store.add_message(con, ticket_id, "ai", "ai", body)
                if ack_id:
                    owner = self._user(ticket["owner_id"])
                    refresh_pending(con, ack_id, {
                        "template": "ticket_received", "to": owner["email"], "ticket_id": ticket_id,
                        "context": {"name": owner.get("name"), "route": route_name, "steps": len(customer_steps),
                                    "issue": triage.get("intent_label"), "severity": triage["severity"],
                                    "status_label": "Solution ready" if status == "self_service" else "With support",
                                    "url": f"{self.s.settings.app_url}/tickets/{ticket_id}"}})

        await asyncio.to_thread(_write)
        await self._save_trace(result["trace_id"], ticket_id, "analysis", result)
        await self._post_analysis(ticket, triage, vector, result)
        self._notify(ticket, "analyzed", route=route_name)
        if route_name != "self_service":
            self._spawn(self.refresh_copilot(ticket_id))

    async def _post_analysis(self, ticket: dict, triage: dict, vector: list[float] | None, result: dict) -> None:
        reason = None
        if triage["intent"] == "other":
            reason = "unclassified"
        elif (ticket.get("intake") or {}).get("chose_other"):
            reason = "customer_chose_other"
        elif result["top_similarity"] < self.s.settings.ood_similarity:
            reason = "out_of_distribution"
        elif triage.get("confidence", 1) < 0.45:
            reason = "low_confidence"
        if reason:
            await asyncio.to_thread(self.s.discovery.add, ticket["id"], ticket["complaint_redacted"], reason, vector)
            metrics.inc("discovery_pool_added", reason=reason)
        hit = await asyncio.to_thread(self.s.incidents.check, ticket["id"], triage["intent"], ticket.get("region"),
                                      vector, triage.get("intent_label"))
        if hit:
            members = hit.get("members") or [ticket["id"]]
            for member in members:
                member_ticket = await asyncio.to_thread(self.store.raw, member)
                if not member_ticket:
                    continue

                def _link(mt=member_ticket) -> None:
                    with self.s.db.tx() as con:
                        self.store.event(con, mt["id"], "system", "system", "incident_linked",
                                         {"incident_id": hit["incident_id"], "size": hit["size"]})
                        self.store.add_message(con, mt["id"], "system", "system",
                                               "We've detected an issue affecting several customers in your area. "
                                               "Your ticket is linked to it and we'll update you when it's fixed.")
                        self._email(con, mt, "incident_linked", incident_id=hit["incident_id"],
                                    region=ticket.get("region"))

                await asyncio.to_thread(_link)
                self._notify(member_ticket, "incident_linked", incident_id=hit["incident_id"])
            metrics.inc("incidents", outcome="created" if hit["created"] else "joined")

    # ------------------------------------------------------------------ customer actions
    async def step_feedback(self, user: dict, ticket_id: str, step_id: str, status: str, note: str | None) -> dict:
        ticket = await self._own(ticket_id, user)
        step = next((s for s in ticket["steps"] if s["id"] == step_id and s["customer_visible"]), None)
        if not step:
            raise NotFound(step_id)
        if ticket.get("route") == "human" and step.get("origin") == "ai":
            raise InvalidTransition("These are information-only checks while an admin handles the fix")
        if ticket["status"] not in OPEN:
            raise InvalidTransition("This ticket is no longer open")
        escalate = False

        def _write() -> None:
            nonlocal escalate
            with self.s.db.tx() as con:
                self.store.set_step_status(con, step_id, status, note)
                self.store.event(con, ticket_id, user["id"], "customer", "step_feedback",
                                 {"step": step["position"], "text": step["text"][:120], "status": status,
                                  "note": note})
                visible = [s for s in ticket["steps"] if s["customer_visible"]
                           and s["plan_version"] == step["plan_version"]]
                statuses = {s["id"]: s["status"] for s in visible}
                statuses[step_id] = status
                if ticket["status"] == "self_service" and visible and all(v == "did_not_work"
                                                                          for v in statuses.values()):
                    escalate = True
                    self.store.transition(con, ticket_id, "escalated", "system", "system",
                                          {"reason": "Customer tried every suggested step without success"})
                    self.store.add_message(con, ticket_id, "system", "system",
                                           "None of the steps worked, so we've passed your ticket to an admin "
                                           "along with everything you tried. You won't need to repeat yourself.")

        await asyncio.to_thread(_write)
        metrics.inc("step_feedback", status=status)
        self._spawn(self._learn_from_step(step, status))
        self._notify(ticket, "step_feedback", step_id=step_id, status=status, escalated=escalate)
        if escalate:
            self._spawn(self.refresh_copilot(ticket_id))
        return await self.customer_ticket(user, ticket_id)

    async def _learn_from_step(self, step: dict, status: str) -> None:
        """Outcome-weighted retrieval: nudge the cited past tickets up or down from real customer outcomes."""
        delta = 0.03 if status == "worked" else -0.03
        for cid in step.get("citations") or []:
            if cid.startswith("KB-"):
                continue
            with self.s.db.read() as con:
                current = con.execute(sa.select(corpus_tickets.c.outcome_score).where(
                    corpus_tickets.c.ticket_id == cid)).scalar()
            if current is not None:
                await self.s.indexer.set_outcome(cid, round(min(1.0, max(0.1, current + delta)), 3))

    async def step_chat(self, user: dict, ticket_id: str, step_id: str, text: str) -> dict:
        ticket = await self._own(ticket_id, user)
        step = next((s for s in ticket["steps"] if s["id"] == step_id and s["customer_visible"]), None)
        if not step:
            raise NotFound(step_id)
        redacted, _ = redact(text)
        history = [m for m in ticket["messages"] if m.get("step_id") == step_id]

        def _q() -> None:
            with self.s.db.tx() as con:
                self.store.add_message(con, ticket_id, user["id"], "customer", text, step_id=step_id)

        await asyncio.to_thread(_q)
        source_text = "\n".join(s.get("snippet") or "" for s in (ticket.get("sources") or [])
                                if s["id"] in (step.get("citations") or []))
        if ticket.get("route") == "human" and step.get("origin") == "ai":
            answer = {"reply": "An admin is handling the fix. Please share any observations in the ticket conversation.",
                      "needs_human": True}
        else:
            answer = await self.s.step_chat.reply(step, source_text, history, redacted,
                                                  (ticket.get("triage") or {}).get("language") or "en", ticket["trace_id"])
        escalate = answer["needs_human"] and ticket["status"] == "self_service"

        def _a() -> None:
            with self.s.db.tx() as con:
                self.store.add_message(con, ticket_id, "ai", "ai", answer["reply"], step_id=step_id)
                if answer["needs_human"]:
                    self.store.event(con, ticket_id, "system", "system", "needs_human",
                                     {"step": step["position"], "question": redacted[:200]})
                if escalate:
                    self.store.transition(con, ticket_id, "escalated", "system", "system",
                                          {"reason": "Customer needs help with a step"})

        await asyncio.to_thread(_a)
        metrics.inc("step_chat", needs_human=str(answer["needs_human"]).lower())
        self._notify(ticket, "message", step_id=step_id)
        if escalate:
            self._spawn(self.refresh_copilot(ticket_id))
        return await self.customer_ticket(user, ticket_id)

    async def customer_message(self, user: dict, ticket_id: str, body: str, answered_option: str | None,
                               reply_to: str | None) -> dict:
        ticket = await self._own(ticket_id, user)
        if ticket["status"] in ("closed",):
            raise InvalidTransition("This ticket is closed")

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.add_message(con, ticket_id, user["id"], "customer", body, answered_option=answered_option)
                if ticket["status"] == "awaiting_customer":
                    target = "in_progress" if ticket.get("assignee_id") else "escalated"
                    self.store.transition(con, ticket_id, target, user["id"], "customer",
                                          {"reason": "Customer replied"})

        await asyncio.to_thread(_write)
        self._notify(ticket, "message")
        return await self.customer_ticket(user, ticket_id)

    async def request_human(self, user: dict, ticket_id: str, reason: str) -> dict:
        ticket = await self._own(ticket_id, user)
        if ticket["status"] != "self_service":
            return await self.customer_ticket(user, ticket_id)

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.transition(con, ticket_id, "escalated", user["id"], "customer",
                                      {"reason": reason or "Customer asked for an admin"})
                self.store.event(con, ticket_id, user["id"], "customer", "escalated", {"reason": reason})

        await asyncio.to_thread(_write)
        self._notify(ticket, "escalated")
        self._spawn(self.refresh_copilot(ticket_id))
        return await self.customer_ticket(user, ticket_id)

    async def customer_confirm(self, user: dict, ticket_id: str, solved: bool, note: str) -> dict:
        ticket = await self._own(ticket_id, user)
        if solved:
            if ticket["status"] not in OPEN:
                raise InvalidTransition("Ticket is not open")

            def _resolve() -> None:
                with self.s.db.tx() as con:
                    self.store.transition(con, ticket_id, "resolved", user["id"], "customer",
                                          {"note": note, "confirmed_by": "customer"})
                    self.store.event(con, ticket_id, user["id"], "customer", "resolved", {"confirmed": True})
                    enqueue(con, "learn", {"ticket_id": ticket_id, "confirmed": True})

            await asyncio.to_thread(_resolve)
            metrics.inc("resolutions", by="customer", route=ticket.get("route") or "?")
        else:
            await self._reopen(ticket, user, note)
        self._notify(ticket, "status")
        return await self.customer_ticket(user, ticket_id)

    async def _reopen(self, ticket: dict, user: dict, note: str) -> None:
        if ticket["status"] not in ("self_service", "solution_proposed", "resolved", "awaiting_customer"):
            raise InvalidTransition("Nothing to reopen - the ticket is already with an admin")
        target = "escalated" if ticket["status"] == "self_service" or not ticket.get("assignee_id") else "in_progress"
        was_resolved = ticket["status"] == "resolved"

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.transition(con, ticket["id"], target, user["id"], "customer",
                                      {"reason": note or "Customer reports the solution did not work"},
                                      reopen_count=ticket.get("reopen_count", 0) + 1, resolved_at=None)
                self.store.event(con, ticket["id"], user["id"], "customer", "reopened", {"note": note})
                self.store.add_message(con, ticket["id"], user["id"], "customer",
                                       f"The solution didn't work. {note}".strip())
                self._email(con, ticket, "reopened", status_label="Reopened")

        await asyncio.to_thread(_write)
        metrics.inc("reopens")
        if was_resolved:
            await self.s.indexer.set_outcome(f"LRN-{ticket['id']}", 0.2)  # learned fix proved unreliable
        self._spawn(self.refresh_copilot(ticket["id"]))

    async def feedback(self, user: dict, ticket_id: str, rating: int, comment: str) -> dict:
        await self._own(ticket_id, user)
        await asyncio.to_thread(self.store.save_feedback, ticket_id, user["id"], rating, comment)
        metrics.inc("csat", rating=str(rating))
        return await self.customer_ticket(user, ticket_id)

    async def customer_ticket(self, user: dict, ticket_id: str) -> dict:
        return self.store.customer_view(await self._own(ticket_id, user))

    # ------------------------------------------------------------------ admin actions
    async def admin_ticket(self, ticket_id: str) -> dict:
        ticket = await asyncio.to_thread(self.store.full, ticket_id)
        if not ticket:
            raise NotFound(ticket_id)
        return ticket

    async def claim(self, admin: dict, ticket_id: str) -> dict:
        ticket = await self.admin_ticket(ticket_id)

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.update(con, ticket_id, assignee_id=admin["id"])
                self.store.event(con, ticket_id, admin["id"], admin["role"], "assigned",
                                 {"admin": admin.get("name") or admin["email"]})
                if ticket["status"] in ("escalated", "self_service"):
                    self.store.transition(con, ticket_id, "in_progress", admin["id"], admin["role"])

        await asyncio.to_thread(_write)
        self._notify(ticket, "assigned")
        return await self.admin_ticket(ticket_id)

    async def admin_message(self, admin: dict, ticket_id: str, body: str, options: list[str], internal: bool,
                            request_info: bool) -> dict:
        ticket = await self.admin_ticket(ticket_id)
        if ticket["status"] == "closed":
            raise InvalidTransition("Ticket is closed")

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.add_message(con, ticket_id, admin["id"], "admin", body, options=options or None,
                                       visibility="internal" if internal else "public")
                if internal:
                    return
                if not ticket.get("assignee_id"):
                    self.store.update(con, ticket_id, assignee_id=admin["id"])
                if request_info and ticket["status"] in ("escalated", "in_progress", "self_service",
                                                         "solution_proposed"):
                    if ticket["status"] == "self_service":
                        self.store.transition(con, ticket_id, "in_progress", admin["id"], admin["role"])
                    self.store.transition(con, ticket_id, "awaiting_customer", admin["id"], admin["role"],
                                          {"reason": "More information requested"})
                    self.store.event(con, ticket_id, admin["id"], admin["role"], "info_requested",
                                     {"options": options})
                elif ticket["status"] == "escalated":
                    self.store.transition(con, ticket_id, "in_progress", admin["id"], admin["role"])
                self._email(con, ticket, "admin_message", message=body, options=options)

        await asyncio.to_thread(_write)
        self._notify(ticket, "message")
        return await self.admin_ticket(ticket_id)

    async def propose_solution(self, admin: dict, ticket_id: str, step_texts: list[str], message: str) -> dict:
        ticket = await self.admin_ticket(ticket_id)
        plan = max((s["plan_version"] for s in ticket["steps"]), default=0) + 1

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.replace_steps(con, ticket_id, "admin", [{"text": t} for t in step_texts if t.strip()],
                                         True, plan)
                if message.strip():
                    self.store.add_message(con, ticket_id, admin["id"], "admin", message)
                if ticket["status"] in ("escalated", "self_service"):
                    self.store.transition(con, ticket_id, "in_progress", admin["id"], admin["role"])
                self.store.transition(con, ticket_id, "solution_proposed", admin["id"], admin["role"],
                                      {"steps": len(step_texts)}, assignee_id=ticket.get("assignee_id") or admin["id"])
                self.store.event(con, ticket_id, admin["id"], admin["role"], "solution_proposed",
                                 {"steps": step_texts})
                self._email(con, ticket, "solution_proposed", steps=step_texts)

        await asyncio.to_thread(_write)
        self._notify(ticket, "solution_proposed")
        return await self.admin_ticket(ticket_id)

    async def admin_resolve(self, admin: dict, ticket_id: str, note: str) -> dict:
        ticket = await self.admin_ticket(ticket_id)
        if not note.strip():
            raise ValueError("A resolution note is required")

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.transition(con, ticket_id, "resolved", admin["id"], admin["role"],
                                      {"note": note, "confirmed_by": "admin"})
                self.store.add_message(con, ticket_id, admin["id"], "admin", note, visibility="internal")
                self.store.event(con, ticket_id, admin["id"], admin["role"], "resolved", {"confirmed": False})
                enqueue(con, "learn", {"ticket_id": ticket_id, "confirmed": False, "note": note})

        await asyncio.to_thread(_write)
        metrics.inc("resolutions", by="admin", route=ticket.get("route") or "?")
        self._notify(ticket, "status")
        return await self.admin_ticket(ticket_id)

    async def refresh_copilot(self, ticket_id: str) -> dict:
        ticket = await asyncio.to_thread(self.store.full, ticket_id)
        if not ticket:
            raise NotFound(ticket_id)
        failed = [s["text"] for s in ticket["steps"] if s["status"] == "did_not_work"]
        query = ticket["complaint_redacted"] + ("\nAlready tried without success: " + "; ".join(failed)
                                                if failed else "")
        retrieval = await self.s.retriever.search(query, top_tickets=8, top_kb=4)
        sources = await self.s.resolver.evidence(retrieval, ticket.get("triage") or {})
        timeline = {
            "steps": [{"text": s["text"], "status": s["status"], "note": s.get("status_note"), "origin": s["origin"]}
                      for s in ticket["steps"]],
            "messages": [{"from": m["author_role"], "text": m["body"][:500], "step": bool(m.get("step_id"))}
                         for m in ticket["messages"][-20:]],
            "events": [{"kind": e["kind"], "detail": e["detail"]} for e in ticket["events"][-20:]],
        }
        brief = await self.s.copilot.brief(ticket, timeline, sources, ticket.get("trace_id") or ticket_id)
        brief["generated_at"] = utc_now().isoformat()
        brief["sources"] = [{k: s.get(k) for k in ("id", "kind", "title", "snippet", "similarity", "audience")}
                            for s in sources]

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.update(con, ticket_id, copilot=brief)

        await asyncio.to_thread(_write)
        self.bus.publish(["admins"], "copilot", {"ticket_id": ticket_id})
        return brief

    async def resolve_incident(self, admin: dict, incident_id: str, note: str) -> dict:
        from ..db import incidents as incidents_table

        with self.s.db.read() as con:
            members = [r[0] for r in con.execute(sa.select(tickets.c.id).where(
                tickets.c.incident_id == incident_id, tickets.c.status.in_(list(OPEN))))]
        with self.s.db.tx() as con:
            con.execute(incidents_table.update().where(incidents_table.c.id == incident_id).values(
                status="resolved", resolved_at=utc_now(), public_note=note))
        for member in members:
            await self.propose_solution(admin, member, [
                "Check whether your service is working again now.",
                "If it is still not working, restart your router or phone once and check again."],
                f"Update on the area issue: {note} Please confirm whether your service is back.")
        return {"incident_id": incident_id, "tickets_updated": len(members)}

    # ------------------------------------------------------------------ learning loop
    async def _learn_handler(self, payload: dict) -> None:
        await self.learn(payload["ticket_id"], bool(payload.get("confirmed")), payload.get("note"))

    async def learn(self, ticket_id: str, confirmed: bool, note: str | None = None) -> dict:
        ticket = await asyncio.to_thread(self.store.full, ticket_id)
        if not ticket:
            raise NotFound(ticket_id)
        ticket["resolution_note"] = note
        timeline = {
            "steps": [{"text": s["text"], "status": s["status"], "origin": s["origin"], "note": s.get("status_note")}
                      for s in ticket["steps"]],
            "messages": [{"from": m["author_role"], "text": m["body"][:600]} for m in ticket["messages"]
                         if m["visibility"] == "public" or m["author_role"] not in ("customer", "ai", "system")][-25:],
            "admin_note": note,
        }
        summary = await self.s.summarizer.summarize(ticket, timeline, ticket.get("trace_id"))
        triage = ticket.get("triage") or {}
        learned_id = f"LRN-{ticket_id}"
        record = {
            "ticket_id": learned_id, "record_version": 1 + int(ticket.get("reopen_count") or 0),
            "subject": summary["title"], "body": f"{ticket['complaint_redacted'][:800]}\n{summary['problem']}",
            "resolution_summary": summary["root_cause"], "resolution_steps": summary["resolution_steps"],
            "intent": triage.get("intent"), "product": triage.get("product"), "category": triage.get("category"),
            "severity": triage.get("severity"), "language": triage.get("language"), "status": "resolved",
            "resolved_at": datetime.now(UTC).isoformat(), "closure_evidence": "customer confirmed" if confirmed
            else "admin resolution note", "source_kind": "learned", "outcome_score": 0.9 if confirmed else 0.7,
        }
        report = await self.s.indexer.index_tickets([record], source="learned")
        kb_hits = await self.s.retriever.search(f"{summary['title']}. {summary['problem']}", top_tickets=1, top_kb=3,
                                                rerank=False)
        best_kb = max((s.get("similarity") or 0 for s in kb_hits.kb), default=0.0)
        kb_draft = None
        if best_kb < 0.80 or triage.get("intent") in (None, "other"):
            kb_draft = f"KB-LRN-{ticket_id[-6:]}"
            await self.s.indexer.upsert_kb({
                "kb_id": kb_draft, "version": 1, "title": summary["title"], "product": triage.get("product"),
                "intent": triage.get("intent"), "summary": f"Learned from {ticket_id}. Root cause: "
                f"{summary['root_cause']}", "checks": summary["resolution_steps"],
                "self_help": summary.get("customer_self_help") or [], "escalation": summary.get("escalation_criteria"),
                "status": "draft", "origin": "learned", "source_ticket_id": ticket_id,
                "review_reason": f"New fix not covered by existing KB (closest similarity {best_kb:.2f})"},
                actor="system")

        def _write() -> None:
            with self.s.db.tx() as con:
                self.store.update(con, ticket_id, resolution_summary={**summary, "learned_record": learned_id,
                                                                      "kb_draft": kb_draft,
                                                                      "closest_kb_similarity": round(best_kb, 3)})
                self.store.event(con, ticket_id, "system", "system", "learned",
                                 {"record": learned_id, "kb_draft": kb_draft, "index": report.as_dict()})
                self._email(con, ticket, "resolved", root_cause=summary["root_cause"],
                            steps=summary["resolution_steps"][:5])

        await asyncio.to_thread(_write)
        metrics.inc("learned_cases")
        self.s.outbox.kick()
        self._notify(ticket, "learned")
        return {"learned_record": learned_id, "kb_draft": kb_draft, "summary": summary}

    # ------------------------------------------------------------------ KB review
    def kb_list(self, status: str | None = None) -> list[dict]:
        query = sa.select(kb_articles).order_by(kb_articles.c.updated_at.desc())
        if status:
            query = query.where(kb_articles.c.status == status)
        with self.s.db.read() as con:
            return [{**dict(r._mapping), "updated_at": r.updated_at.isoformat()} for r in con.execute(query)]

    async def kb_decide(self, actor: dict, kb_id: str, action: str, edits: dict | None) -> dict:
        with self.s.db.read() as con:
            row = con.execute(sa.select(kb_articles).where(kb_articles.c.kb_id == kb_id)).first()
        if not row:
            raise NotFound(kb_id)
        article = {**dict(row._mapping), **(edits or {})}
        status = {"publish": "published", "deprecate": "deprecated", "reject": "rejected"}[action]
        article.update(status=status, version=row.version + 1, review_reason=None)
        result = await self.s.indexer.upsert_kb(article, actor=actor["id"])
        return {"kb_id": kb_id, "status": status, "index": result}

    def queue_counts(self) -> dict:
        with self.s.db.read() as con:
            rows = con.execute(sa.select(tickets.c.status, sa.func.count()).group_by(tickets.c.status)).all()
        counts = {status: count for status, count in rows}
        return {"by_status": counts, "human_queue": sum(counts.get(s, 0) for s in HUMAN_QUEUE),
                "open": sum(counts.get(s, 0) for s in OPEN)}
