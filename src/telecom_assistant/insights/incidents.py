"""Incident radar: detect that many customers are hitting the same live problem.

When a new ticket is analyzed, it is compared with open tickets from the last N hours in the same region:
same intent family and a complaint-embedding cosine >= threshold. Once at least `incident_min_tickets`
match, an incident is opened (or the ticket joins the existing one). Linked customers are told it is a
known issue (no duplicate troubleshooting), agents work one incident instead of N tickets, and resolving
the incident proposes the fix to every linked ticket for confirmation.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import numpy as np
import sqlalchemy as sa

from ..config import Settings
from ..db import Database, bytes_to_vec, incidents, tickets, utc_now, vec_to_bytes
from ..tickets.lifecycle import OPEN

FAMILY = {
    "connectivity.area_outage": "outage", "fiber.loss_of_signal": "outage", "connectivity.intermittent_drop": "outage",
    "connectivity.slow_speed": "degradation", "mobile.data_unavailable": "mobile",
    "mobile.voice_calls_fail": "mobile", "mobile.otp_sms_missing": "sms",
}


def family(intent: str | None) -> str | None:
    return FAMILY.get(intent or "", intent)


class IncidentRadar:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings, self.db = settings, db

    def check(self, ticket_id: str, intent: str | None, region: str | None, vector: list[float] | None,
              label: str | None) -> dict | None:
        """Returns {"incident_id", "created", "size"} when the ticket belongs to an incident."""
        if not region or vector is None or intent in (None, "other"):
            return None
        fam = family(intent)
        since = utc_now() - timedelta(hours=self.settings.incident_window_hours)
        vec = np.asarray(vector, dtype=np.float32)
        vec = vec / (np.linalg.norm(vec) or 1.0)
        with self.db.tx() as con:
            open_incident = con.execute(sa.select(incidents).where(
                incidents.c.status == "open", sa.func.lower(incidents.c.region) == region.casefold())).all()
            for incident in open_incident:
                centroid = bytes_to_vec(incident.centroid)
                if family(incident.intent) == fam and centroid is not None and float(
                        np.dot(vec, centroid / (np.linalg.norm(centroid) or 1.0))) >= self.settings.incident_similarity:
                    con.execute(tickets.update().where(tickets.c.id == ticket_id).values(incident_id=incident.id))
                    size = con.execute(sa.select(sa.func.count()).where(tickets.c.incident_id == incident.id)).scalar()
                    return {"incident_id": incident.id, "created": False, "size": size}
            rows = con.execute(sa.select(tickets.c.id, tickets.c.intent, tickets.c.embedding).where(
                tickets.c.id != ticket_id, tickets.c.created_at >= since, tickets.c.status.in_(list(OPEN)),
                sa.func.lower(tickets.c.region) == region.casefold(), tickets.c.incident_id.is_(None))).all()
            matches = []
            for row in rows:
                other = bytes_to_vec(row.embedding)
                if other is None or family(row.intent) != fam:
                    continue
                if float(np.dot(vec, other / (np.linalg.norm(other) or 1.0))) >= self.settings.incident_similarity:
                    matches.append((row.id, other))
            if len(matches) + 1 < self.settings.incident_min_tickets:
                return None
            members = [ticket_id] + [m[0] for m in matches]
            centroid = np.mean([vec] + [m[1] for m in matches], axis=0)
            incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
            con.execute(incidents.insert().values(
                id=incident_id, title=f"{label or intent} - {region}", intent=intent, region=region,
                status="open", centroid=vec_to_bytes(centroid), created_at=utc_now(),
                public_note="We're aware of an issue affecting customers in your area and are working on it."))
            con.execute(tickets.update().where(tickets.c.id.in_(members)).values(incident_id=incident_id))
            return {"incident_id": incident_id, "created": True, "size": len(members), "members": members}

    def list(self, status: str | None = None) -> list[dict]:
        query = sa.select(incidents).order_by(incidents.c.created_at.desc()).limit(50)
        if status:
            query = query.where(incidents.c.status == status)
        with self.db.read() as con:
            rows = con.execute(query).all()
            counts = dict(con.execute(sa.select(tickets.c.incident_id, sa.func.count()).where(
                tickets.c.incident_id.is_not(None)).group_by(tickets.c.incident_id)).all())
        out = []
        for r in rows:
            data = {k: v for k, v in r._mapping.items() if k != "centroid"}
            for key in ("created_at", "resolved_at"):
                data[key] = data[key].isoformat() if data.get(key) else None
            data["ticket_count"] = counts.get(r.id, 0)
            out.append(data)
        return out
