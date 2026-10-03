"""Transactional outbox + dispatcher.

Side effects (emails, knowledge learning) are written to the ``outbox`` table in the *same database
transaction* as the ticket change that caused them, so a crash can never lose an acknowledgement email
or record one for a change that rolled back. A background dispatcher delivers pending rows:

* ``email`` -> Upstash QStash (when PUBLIC_BASE_URL is reachable; QStash retries + DLQ), else the
  notification service over HTTP (NOTIFY_URL), else in-process (monolith mode);
* other topics -> registered in-process handlers.
Failures back off exponentially (5s, 10s, 20s ... ) and park in ``dead`` after 6 attempts (the DLQ),
from which an admin can replay them.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..config import Settings
from ..db import Database, outbox, utc_now
from ..telemetry import log_event, metrics
from .service import NotificationService

MAX_ATTEMPTS = 6
Handler = Callable[[dict], Awaitable[None]]


def enqueue(con: Connection, topic: str, payload: dict, event_id: str | None = None, delay_s: int = 0) -> str:
    """Write an outbox row inside the caller's transaction (optionally deliverable only after a delay)."""
    event_id = event_id or str(uuid.uuid4())
    now = utc_now()
    con.execute(outbox.insert().values(id=event_id, topic=topic, payload={**payload, "event_id": event_id},
                                       status="pending", attempts=0, next_attempt_at=now + timedelta(seconds=delay_s),
                                       created_at=now))
    return event_id


def refresh_pending(con: Connection, event_id: str, payload: dict) -> bool:
    """Enrich a still-pending event (e.g. add the routing outcome to the acknowledgement) and release it now."""
    result = con.execute(outbox.update().where(outbox.c.id == event_id, outbox.c.status == "pending")
                         .values(payload={**payload, "event_id": event_id}, next_attempt_at=utc_now()))
    return bool(result.rowcount)


class OutboxDispatcher:
    def __init__(self, settings: Settings, db: Database, notifications: NotificationService) -> None:
        self.settings, self.db, self.notifications = settings, db, notifications
        self.handlers: dict[str, Handler] = {"email": self._deliver_email}
        self.wake = asyncio.Event()
        self.client = httpx.AsyncClient(timeout=10)

    def register(self, topic: str, handler: Handler) -> None:
        self.handlers[topic] = handler

    @property
    def channel(self) -> str:
        if self.settings.qstash_token and self.settings.public_base_url:
            return "qstash"
        if self.settings.notify_url:
            return "http"
        return "in-process"

    async def _deliver_email(self, payload: dict) -> None:
        channel = self.channel
        if channel == "qstash":
            destination = f"{self.settings.public_base_url}/notify/v1/notify"
            response = await self.client.post(
                f"https://qstash.upstash.io/v2/publish/{destination}", json=payload,
                headers={"Authorization": f"Bearer {self.settings.qstash_token}", "Upstash-Retries": "3",
                         "Upstash-Deduplication-Id": payload["event_id"]})
            response.raise_for_status()
        elif channel == "http":
            response = await self.client.post(f"{self.settings.notify_url}/v1/notify", json=payload,
                                              headers={"X-Service-Token": self.settings.internal_token()})
            response.raise_for_status()
        else:
            await self.notifications.handle(payload)

    def _due(self, limit: int = 20) -> list:
        with self.db.read() as con:
            return con.execute(sa.select(outbox).where(outbox.c.status == "pending",
                                                       outbox.c.next_attempt_at <= utc_now())
                               .order_by(outbox.c.created_at).limit(limit)).all()

    def _mark(self, row_id: str, **values) -> None:
        with self.db.tx() as con:
            con.execute(outbox.update().where(outbox.c.id == row_id).values(**values))

    async def run_once(self) -> int:
        rows = await asyncio.to_thread(self._due)
        for row in rows:
            handler = self.handlers.get(row.topic)
            try:
                if handler is None:
                    raise RuntimeError(f"No handler for topic {row.topic}")
                await handler(row.payload)
            except Exception as exc:  # noqa: BLE001 - any failure is retried, then dead-lettered
                attempts = row.attempts + 1
                status = "dead" if attempts >= MAX_ATTEMPTS else "pending"
                await asyncio.to_thread(self._mark, row.id, attempts=attempts, status=status,
                                        last_error=f"{type(exc).__name__}: {str(exc)[:400]}",
                                        next_attempt_at=utc_now() + timedelta(seconds=5 * 2 ** attempts))
                metrics.inc("outbox", topic=row.topic, outcome="dead" if status == "dead" else "retry")
                log_event("outbox_failure", topic=row.topic, id=row.id, attempts=attempts, error=str(exc)[:200])
                continue
            await asyncio.to_thread(self._mark, row.id, status="sent", attempts=row.attempts + 1, sent_at=utc_now(),
                                    last_error=None)
            metrics.inc("outbox", topic=row.topic, outcome="sent", channel=self.channel)
        return len(rows)

    async def run(self, interval: float = 2.0) -> None:
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=interval)
            self.wake.clear()
            try:
                while await self.run_once():
                    pass
            except Exception as exc:  # noqa: BLE001 - keep the dispatcher alive
                log_event("outbox_loop_error", error=str(exc)[:300])

    def kick(self) -> None:
        self.wake.set()

    def stats(self) -> dict:
        with self.db.read() as con:
            rows = con.execute(sa.select(outbox.c.topic, outbox.c.status, sa.func.count())
                               .group_by(outbox.c.topic, outbox.c.status)).all()
            dead = con.execute(sa.select(outbox).where(outbox.c.status == "dead")
                               .order_by(outbox.c.created_at.desc()).limit(20)).all()
        return {"counts": [{"topic": t, "status": s, "count": c} for t, s, c in rows], "channel": self.channel,
                "dead_letters": [{"id": r.id, "topic": r.topic, "attempts": r.attempts, "last_error": r.last_error,
                                  "created_at": r.created_at.isoformat() if r.created_at else None} for r in dead]}

    def replay(self, row_id: str | None = None) -> int:
        with self.db.tx() as con:
            query = outbox.update().where(outbox.c.status == "dead")
            if row_id:
                query = query.where(outbox.c.id == row_id)
            result = con.execute(query.values(status="pending", attempts=0, next_attempt_at=utc_now()))
        self.kick()
        return result.rowcount
