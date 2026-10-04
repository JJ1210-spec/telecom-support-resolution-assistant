"""Persistence for tickets, timeline events, conversations and step checklists (synchronous SQLAlchemy;
the async pipeline calls these through a thread pool)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db import (
    Database,
    feedback,
    incidents,
    intake_sessions,
    messages,
    row_dict,
    steps,
    ticket_events,
    tickets,
    users,
    utc_now,
)
from .lifecycle import HUMAN_QUEUE, OPEN, SLA, STATUSES, check_transition

PUBLIC_EVENTS = {"created", "analyzed", "status_changed", "escalated", "reopened", "resolved", "step_feedback",
                 "solution_proposed", "incident_linked", "assigned", "info_requested"}


def new_ticket_id() -> str:
    return f"TCK-{datetime.now(UTC):%y%m}-{uuid.uuid4().hex[:6].upper()}"


class TicketStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ writes
    def event(self, con: Connection, ticket_id: str, actor_id: str, role: str, kind: str, detail: dict | None = None):
        con.execute(ticket_events.insert().values(ticket_id=ticket_id, actor_id=actor_id, actor_role=role, kind=kind,
                                                  detail=detail or {}, created_at=utc_now()))

    def create(self, con: Connection, owner_id: str, complaint: str, redacted: str, product_hint: str | None,
               region: str | None, intake: dict | None, subject: str) -> str:
        ticket_id, now = new_ticket_id(), utc_now()
        con.execute(tickets.insert().values(
            id=ticket_id, owner_id=owner_id, subject=subject[:200], complaint=complaint, complaint_redacted=redacted,
            product_hint=product_hint, region=region, status="analyzing", intake=intake, created_at=now,
            updated_at=now, analysis_state="running"))
        self.event(con, ticket_id, owner_id, "customer", "created", {"subject": subject[:200]})
        return ticket_id

    def transition(self, con: Connection, ticket_id: str, target: str, actor_id: str, role: str,
                   detail: dict | None = None, **values) -> str:
        current = con.execute(sa.select(tickets.c.status).where(tickets.c.id == ticket_id)).scalar_one()
        check_transition(current, target)
        now = utc_now()
        if target == "resolved":
            values.setdefault("resolved_at", now)
        con.execute(tickets.update().where(tickets.c.id == ticket_id).values(status=target, updated_at=now, **values))
        if current != target:
            self.event(con, ticket_id, actor_id, role, "status_changed",
                       {"from": current, "to": target, **(detail or {})})
        return current

    def update(self, con: Connection, ticket_id: str, **values) -> None:
        con.execute(tickets.update().where(tickets.c.id == ticket_id).values(updated_at=utc_now(), **values))

    def add_message(self, con: Connection, ticket_id: str, author_id: str, role: str, body: str,
                    options: list[str] | None = None, step_id: str | None = None, visibility: str = "public",
                    answered_option: str | None = None) -> str:
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        con.execute(messages.insert().values(id=message_id, ticket_id=ticket_id, author_id=author_id,
                                             author_role=role, body=body, options=options or None, step_id=step_id,
                                             visibility=visibility, answered_option=answered_option,
                                             created_at=utc_now()))
        return message_id

    def replace_steps(self, con: Connection, ticket_id: str, origin: str, items: list[dict], customer_visible: bool,
                      plan_version: int) -> list[str]:
        ids = []
        for position, item in enumerate(items, 1):
            step_id = f"stp_{uuid.uuid4().hex[:10]}"
            con.execute(steps.insert().values(
                id=step_id, ticket_id=ticket_id, origin=origin, plan_version=plan_version, position=position,
                text=item["text"], detail=item.get("detail"), citations=item.get("citations") or [],
                customer_visible=customer_visible, status="pending", created_at=utc_now()))
            ids.append(step_id)
        return ids

    def set_step_status(self, con: Connection, step_id: str, status: str, note: str | None) -> dict:
        con.execute(steps.update().where(steps.c.id == step_id).values(status=status, status_note=note,
                                                                      status_at=utc_now()))
        return row_dict(con.execute(sa.select(steps).where(steps.c.id == step_id)).one())

    def save_feedback(self, ticket_id: str, user_id: str, rating: int, comment: str) -> None:
        with self.db.tx() as con:
            exists = con.execute(sa.select(feedback.c.ticket_id).where(feedback.c.ticket_id == ticket_id)).first()
            values = {"user_id": user_id, "rating": rating, "comment": comment, "created_at": utc_now()}
            if exists:
                con.execute(feedback.update().where(feedback.c.ticket_id == ticket_id).values(**values))
            else:
                con.execute(feedback.insert().values(ticket_id=ticket_id, **values))
            self.event(con, ticket_id, user_id, "customer", "feedback", {"rating": rating})

    # ------------------------------------------------------------------ intake sessions
    def save_intake(self, user_id: str, state: dict, session_id: str | None = None) -> str:
        now = utc_now()
        with self.db.tx() as con:
            if session_id:
                con.execute(intake_sessions.update().where(intake_sessions.c.id == session_id,
                                                           intake_sessions.c.user_id == user_id)
                            .values(state=state, updated_at=now))
                return session_id
            session_id = f"int_{uuid.uuid4().hex[:12]}"
            con.execute(intake_sessions.insert().values(id=session_id, user_id=user_id, state=state, created_at=now,
                                                        updated_at=now))
            return session_id

    def get_intake(self, user_id: str, session_id: str) -> dict | None:
        with self.db.read() as con:
            row = con.execute(sa.select(intake_sessions).where(intake_sessions.c.id == session_id,
                                                               intake_sessions.c.user_id == user_id)).first()
        return dict(row._mapping) if row else None

    def link_intake(self, con: Connection, session_id: str, ticket_id: str) -> None:
        con.execute(intake_sessions.update().where(intake_sessions.c.id == session_id).values(ticket_id=ticket_id))

    # ------------------------------------------------------------------ reads
    def raw(self, ticket_id: str) -> dict | None:
        with self.db.read() as con:
            row = con.execute(sa.select(tickets).where(tickets.c.id == ticket_id)).first()
        return row_dict(row) if row else None

    def timeline(self, ticket_id: str) -> dict:
        with self.db.read() as con:
            evs = [row_dict(r) for r in con.execute(sa.select(ticket_events).where(
                ticket_events.c.ticket_id == ticket_id).order_by(ticket_events.c.id))]
            msgs = [row_dict(r) for r in con.execute(sa.select(messages).where(
                messages.c.ticket_id == ticket_id).order_by(messages.c.created_at))]
            stps = [row_dict(r) for r in con.execute(sa.select(steps).where(
                steps.c.ticket_id == ticket_id).order_by(steps.c.plan_version, steps.c.origin, steps.c.position))]
            fb = con.execute(sa.select(feedback).where(feedback.c.ticket_id == ticket_id)).first()
        return {"events": evs, "messages": msgs, "steps": stps, "feedback": row_dict(fb) if fb else None}

    def full(self, ticket_id: str) -> dict | None:
        ticket = self.raw(ticket_id)
        if not ticket:
            return None
        ticket.pop("embedding", None)
        ticket["status_label"] = STATUSES.get(ticket["status"], ticket["status"])
        ticket.update(self.timeline(ticket_id))
        for message in ticket["messages"]:
            if message["author_role"] not in ("customer", "admin", "ai", "system"):
                message["author_role"] = "admin"
        for event in ticket["events"]:
            if event["actor_role"] not in ("customer", "admin", "ai", "system"):
                event["actor_role"] = "admin"
        for step in ticket["steps"]:
            if step["origin"] not in ("ai", "admin"):
                step["origin"] = "admin"
        for source in ticket.get("sources") or []:
            if source.get("audience") != "customer":
                source["audience"] = "admin"
        with self.db.read() as con:
            owner = con.execute(sa.select(users.c.email, users.c.name).where(users.c.id == ticket["owner_id"])).first()
            assignee = (con.execute(sa.select(users.c.email, users.c.name).where(
                users.c.id == ticket["assignee_id"], users.c.role == "admin"))
                        .first() if ticket.get("assignee_id") else None)
            incident = (con.execute(sa.select(incidents).where(incidents.c.id == ticket["incident_id"])).first()
                        if ticket.get("incident_id") else None)
        ticket["customer"] = {"email": owner.email, "name": owner.name} if owner else None
        ticket["assignee"] = {"email": assignee.email, "name": assignee.name} if assignee else None
        if not assignee:
            ticket["assignee_id"] = None
        incident_data = row_dict(incident) if incident else None
        if incident_data:
            incident_data.pop("centroid", None)
        ticket["incident"] = incident_data
        return ticket

    @staticmethod
    def customer_view(ticket: dict) -> dict:
        """What the customer may see: no admin steps, internal notes or private source text."""
        triage = ticket.get("triage") or {}
        decision = ticket.get("decision") or {}
        visible_steps = [s for s in ticket.get("steps", []) if s.get("customer_visible")]
        latest_plan = max((s["plan_version"] for s in visible_steps), default=0)
        sources = {s["id"]: s for s in ticket.get("sources") or [] if s.get("id")}

        def public_evidence(step: dict) -> list[dict]:
            evidence = []
            for citation in dict.fromkeys(step.get("citations") or []):
                source = sources.get(citation)
                if not source:
                    continue
                if source.get("kind") == "kb" and source.get("audience") == "customer":
                    evidence.append({"id": citation, "kind": "guide", "title": source.get("title") or "Support guide",
                                     "excerpt": source.get("snippet") or ""})
                elif source.get("kind") == "ticket":
                    # Historical tickets may contain another customer's details or admin-only actions.
                    evidence.append({"id": citation, "kind": "case", "title": "Past resolved support case"})
            return evidence

        return {
            "id": ticket["id"], "subject": ticket["subject"], "complaint": ticket["complaint"],
            "status": ticket["status"], "status_label": ticket["status_label"], "route": ticket.get("route"),
            "created_at": ticket["created_at"], "updated_at": ticket["updated_at"],
            "resolved_at": ticket.get("resolved_at"), "reopen_count": ticket.get("reopen_count", 0),
            "analysis_state": ticket.get("analysis_state"), "region": ticket.get("region"),
            "issue": {"label": triage.get("intent_label"), "area": triage.get("area"), "severity": triage.get("severity"),
                      "product": triage.get("product")} if triage else None,
            "why": decision.get("customer_reason"), "recurrence": decision.get("recurrence"),
            "sla_due_at": ticket.get("sla_due_at"),
            "steps": [{**{k: s[k] for k in ("id", "position", "text", "detail", "status", "status_note", "origin",
                                              "citations", "plan_version")}, "evidence": public_evidence(s)}
                      for s in visible_steps if s["plan_version"] == latest_plan],
            "messages": [{k: m[k] for k in ("id", "author_role", "body", "options", "answered_option", "step_id",
                                            "created_at")}
                         for m in ticket.get("messages", []) if m["visibility"] == "public"],
            "events": [{k: e[k] for k in ("kind", "actor_role", "detail", "created_at")}
                       for e in ticket.get("events", []) if e["kind"] in PUBLIC_EVENTS],
            "feedback": ticket.get("feedback"), "assignee": (ticket.get("assignee") or {}).get("name") or None,
            "incident": ({k: ticket["incident"][k] for k in ("id", "title", "status", "public_note")}
                         if ticket.get("incident") else None),
            "resolution": ({k: (ticket.get("resolution_summary") or {}).get(k)
                            for k in ("title", "root_cause", "resolution_steps")}
                           if ticket.get("resolution_summary") else None),
        }

    def list(self, *, owner_id: str | None = None, statuses: set[str] | None = None, limit: int = 200,
             assignee_id: str | None = None) -> list[dict]:
        query = sa.select(tickets.c.id, tickets.c.subject, tickets.c.status, tickets.c.route, tickets.c.severity,
                          tickets.c.intent, tickets.c.product, tickets.c.sentiment, tickets.c.confidence,
                          tickets.c.created_at, tickets.c.updated_at, tickets.c.sla_due_at, tickets.c.reopen_count,
                          tickets.c.assignee_id, tickets.c.incident_id, tickets.c.region, tickets.c.analysis_state,
                          tickets.c.triage, users.c.email.label("customer_email")).join(
            users, users.c.id == tickets.c.owner_id).order_by(tickets.c.created_at.desc()).limit(limit)
        if owner_id:
            query = query.where(tickets.c.owner_id == owner_id)
        if statuses:
            query = query.where(tickets.c.status.in_(list(statuses)))
        if assignee_id:
            query = query.where(tickets.c.assignee_id == assignee_id)
        with self.db.read() as con:
            rows = [row_dict(r) for r in con.execute(query)]
        now = datetime.now(UTC)
        for row in rows:
            triage = row.pop("triage") or {}
            row["intent_label"] = triage.get("intent_label")
            row["churn_risk"] = triage.get("churn_risk")
            row["status_label"] = STATUSES.get(row["status"], row["status"])
            due = row.get("sla_due_at")
            if due and row["status"] in OPEN:
                remaining = (datetime.fromisoformat(due) - now).total_seconds() / 3600
                row["sla_hours_left"] = round(remaining, 1)
            row["in_human_queue"] = row["status"] in HUMAN_QUEUE
        return rows

    @staticmethod
    def sla_due(severity: str | None, created_at: datetime | None = None):
        return (created_at or utc_now()) + SLA.get(severity or "P3", SLA["P3"])
