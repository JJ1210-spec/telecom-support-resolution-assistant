"""Operational KPIs for the agent/admin overview: deflection, resolution time, reopen rate, CSAT, step success."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime

import sqlalchemy as sa

from ..db import Database, feedback, steps, ticket_events, tickets


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def overview(db: Database) -> dict:
    with db.read() as con:
        rows = con.execute(sa.select(tickets.c.id, tickets.c.status, tickets.c.route, tickets.c.severity,
                                     tickets.c.intent, tickets.c.created_at, tickets.c.resolved_at,
                                     tickets.c.reopen_count, tickets.c.sla_due_at, tickets.c.triage)).all()
        human_touched = {r.ticket_id for r in con.execute(sa.select(ticket_events.c.ticket_id, ticket_events.c.detail)
                                                          .where(ticket_events.c.kind == "status_changed"))
                         if (r.detail or {}).get("to") in ("escalated", "in_progress")}
        ratings = [r[0] for r in con.execute(sa.select(feedback.c.rating))]
        step_rows = con.execute(sa.select(steps.c.status, steps.c.origin).where(
            steps.c.customer_visible.is_(True))).all()
        daily = con.execute(sa.select(tickets.c.created_at, tickets.c.route)).all()
    total = len(rows)
    by_route = Counter(r.route or "pending" for r in rows)
    by_status = Counter(r.status for r in rows)
    by_severity = Counter(r.severity or "-" for r in rows)
    self_service = [r for r in rows if r.route == "self_service"]
    deflected = [r for r in self_service if r.status in ("resolved", "closed") and r.id not in human_touched]
    resolved = [r for r in rows if r.resolved_at]
    hours = sorted(((_aware(r.resolved_at) - _aware(r.created_at)).total_seconds() / 3600) for r in resolved)
    now = datetime.now(UTC)
    breaches = sum(1 for r in rows if r.status not in ("resolved", "closed") and r.sla_due_at
                   and _aware(r.sla_due_at) < now)
    step_counts = Counter(s.status for s in step_rows)
    tried = step_counts["worked"] + step_counts["did_not_work"]
    per_day: dict[str, Counter] = defaultdict(Counter)
    for created, route_name in daily:
        per_day[_aware(created).strftime("%Y-%m-%d")][route_name or "pending"] += 1
    top_intents = Counter((r.triage or {}).get("intent_label") or r.intent or "Unclassified" for r in rows)
    return {
        "tickets": total, "by_route": dict(by_route), "by_status": dict(by_status), "by_severity": dict(by_severity),
        "self_service_rate": round(len(self_service) / total, 3) if total else 0.0,
        "deflection_rate": round(len(deflected) / total, 3) if total else 0.0,
        "self_service_success": round(len(deflected) / len(self_service), 3) if self_service else None,
        "median_hours_to_resolve": round(hours[len(hours) // 2], 2) if hours else None,
        "reopen_rate": round(sum(1 for r in rows if (r.reopen_count or 0) > 0) / total, 3) if total else 0.0,
        "csat": round(sum(ratings) / len(ratings), 2) if ratings else None, "csat_responses": len(ratings),
        "step_success_rate": round(step_counts["worked"] / tried, 3) if tried else None,
        "steps_tried": tried, "sla_breaches": breaches,
        "per_day": [{"day": day, **counts} for day, counts in sorted(per_day.items())[-14:]],
        "top_issues": [{"label": k, "count": v} for k, v in top_intents.most_common(8)],
    }
