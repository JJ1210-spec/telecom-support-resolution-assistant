"""Notification service: an independently deployable FastAPI app that renders and sends emails.

* Input: notification events ``{event_id, template, to, ticket_id, context}`` pushed by the core API's
  transactional outbox - via Upstash QStash (signature-verified), a direct HTTP call with the internal
  service token, or in-process in monolith mode.
* Idempotent: ``event_id`` is the primary key of ``email_log``; QStash or outbox retries never send twice.
* Transports: ``outbox`` (capture only - the admin UI shows every rendered email), ``smtp`` or ``resend``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import smtplib
import time
from email.message import EmailMessage

import httpx
import sqlalchemy as sa
from fastapi import FastAPI, Header, HTTPException, Request

from ..config import Settings
from ..db import Database, email_log, utc_now
from ..telemetry import metrics
from .templates import render


class DeliveryError(RuntimeError):
    pass


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify_qstash_signature(token: str | None, body: bytes, url: str, keys: list[str]) -> bool:
    """Verify an Upstash-Signature JWT (HS256) against the current or next signing key."""
    if not token or token.count(".") != 2:
        return False
    header_b64, payload_b64, signature_b64 = token.split(".")
    signed = f"{header_b64}.{payload_b64}".encode()
    try:
        signature = _b64url_decode(signature_b64)
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return False
    if not any(key and hmac.compare_digest(hmac.new(key.encode(), signed, hashlib.sha256).digest(), signature)
               for key in keys):
        return False
    now = time.time()
    if claims.get("iss") != "Upstash" or claims.get("exp", 0) < now - 5 or claims.get("nbf", 0) > now + 5:
        return False
    if url and claims.get("sub") and claims["sub"].rstrip("/") != url.rstrip("/"):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
    return str(claims.get("body", "")).rstrip("=") == digest


class NotificationService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings, self.db = settings, db

    def _send_smtp(self, to: str, subject: str, html: str, text: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.settings.email_from, to, subject
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if self.settings.smtp_user:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.send_message(message)

    async def _send_resend(self, to: str, subject: str, html: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://api.resend.com/emails",
                                         headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
                                         json={"from": self.settings.email_from, "to": [to], "subject": subject,
                                               "html": html, "text": text})
        if response.status_code >= 300:
            raise DeliveryError(f"Resend rejected email: {response.status_code} {response.text[:200]}")

    async def handle(self, event: dict) -> dict:
        event_id = str(event["event_id"])
        with self.db.read() as con:
            existing = con.execute(sa.select(email_log.c.status).where(email_log.c.event_id == event_id)).first()
        if existing and existing.status in ("sent", "captured"):
            metrics.inc("emails", outcome="duplicate")
            return {"event_id": event_id, "status": existing.status, "duplicate": True}
        subject, html, text = render(event["template"], {**event.get("context", {}),
                                                         "ticket_id": event.get("ticket_id", "")})
        transport = self.settings.email_transport
        status, error = "captured", None
        try:
            if transport == "smtp" and self.settings.smtp_host:
                await asyncio.to_thread(self._send_smtp, event["to"], subject, html, text)
                status = "sent"
            elif transport == "resend" and self.settings.resend_api_key:
                await self._send_resend(event["to"], subject, html, text)
                status = "sent"
        except (smtplib.SMTPException, OSError, DeliveryError, httpx.HTTPError) as exc:
            status, error = "failed", str(exc)[:500]
        values = {"ticket_id": event.get("ticket_id"), "to_address": event["to"], "template": event["template"],
                  "subject": subject, "html": html, "text": text, "transport": transport, "status": status,
                  "error": error, "created_at": utc_now()}
        with self.db.tx() as con:
            if existing:
                con.execute(email_log.update().where(email_log.c.event_id == event_id).values(**values))
            else:
                con.execute(email_log.insert().values(event_id=event_id, **values))
        metrics.inc("emails", outcome=status, template=event["template"])
        if status == "failed":
            raise DeliveryError(error or "delivery failed")
        return {"event_id": event_id, "status": status}

    def list(self, ticket_id: str | None = None, limit: int = 50) -> list[dict]:
        query = sa.select(email_log).order_by(email_log.c.created_at.desc()).limit(limit)
        if ticket_id:
            query = query.where(email_log.c.ticket_id == ticket_id)
        with self.db.read() as con:
            rows = con.execute(query).all()
        return [{**dict(r._mapping), "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


def create_notify_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if db is None:
        db = Database(settings.sqlalchemy_url)
        db.create_all()
    service = NotificationService(settings, db)
    app = FastAPI(title="Notification Service", version="1.0.0")
    app.state.service = service

    def authorize(request: Request, body: bytes, token: str | None, signature: str | None) -> None:
        if token and secrets.compare_digest(token, settings.internal_token()):
            return
        keys = [settings.qstash_current_signing_key, settings.qstash_next_signing_key]
        if verify_qstash_signature(signature, body, str(request.url), keys):
            return
        raise HTTPException(401, "Service token or valid QStash signature required")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "transport": settings.email_transport}

    @app.post("/v1/notify")
    async def notify(request: Request, x_service_token: str | None = Header(default=None),
                     upstash_signature: str | None = Header(default=None)) -> dict:
        body = await request.body()
        authorize(request, body, x_service_token, upstash_signature)
        try:
            event = json.loads(body)
            return await service.handle(event)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, f"Invalid notification event: {exc}") from exc
        except DeliveryError as exc:
            raise HTTPException(502, str(exc)) from exc  # non-2xx -> QStash / outbox retry with backoff

    @app.get("/v1/emails")
    def emails(ticket_id: str | None = None, x_service_token: str | None = Header(default=None)) -> dict:
        if not x_service_token or not secrets.compare_digest(x_service_token, settings.internal_token()):
            raise HTTPException(401, "Service token required")
        return {"emails": service.list(ticket_id)}

    return app
