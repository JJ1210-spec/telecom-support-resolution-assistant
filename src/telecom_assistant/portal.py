"""Authenticated customer tickets and administrator review workflow."""

# FastAPI evaluates Depends(...) defaults when registering routes.
# ruff: noqa: B008

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import Settings
from .schemas import ResolveInput

WEB = Path(__file__).resolve().parents[2] / "web"
SESSION_SECONDS = 12 * 60 * 60
WORD = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
EMAIL = re.compile(r"(?<!\w)[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")
LONG_NUMBER = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)")
UNSAFE_PROMISE = re.compile(r"\b(refund|free|guarantee|promise|compensation|within \d+ (?:hours?|days?))\b", re.IGNORECASE)
UNSAFE_CUSTOMER_ACTION = re.compile(
    r"\b(dispatch|reprovision|provision|line test|optical signal test|account reset|factory reset|payment|replace equipment)\b",
    re.IGNORECASE,
)
STOP_WORDS = {"the", "and", "for", "with", "that", "this", "your", "then", "check", "please",
              "service", "from", "away", "less", "test"}
SYNONYMS = {"move": "placement", "obstructions": "walls", "ethernet": "wired",
            "stability": "connection", "stable": "connection"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def redact_for_model(text: str) -> str:
    """Remove common identifiers before sending a complaint to local AI/search."""
    return LONG_NUMBER.sub("[NUMBER REDACTED]", EMAIL.sub("[EMAIL REDACTED]", text))


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, expected = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def customer_steps(analysis: dict) -> list[dict]:
    """Release only low-severity, KB-supported checks to customers."""
    triage = analysis.get("triage") or {}
    if analysis.get("decision") != "suggested_resolution" or triage.get("severity") not in {"P3", "P4"}:
        return []
    sources = {s["source_id"]: s for s in analysis.get("sources", []) if s.get("kind") == "kb"}
    approved = []
    for step in analysis.get("steps", []):
        if UNSAFE_PROMISE.search(step.get("text", "")) or UNSAFE_CUSTOMER_ACTION.search(step.get("text", "")):
            continue
        words = {SYNONYMS.get(word, word) for word in WORD.findall(step.get("text", "").casefold())}
        words -= STOP_WORDS
        supported = []
        excerpt = ""
        for source_id, source in sources.items():
            if source.get("score", 0) < 0.60:
                continue
            # Knowledge text is title, product, summary, then checks and escalation criteria.
            # A title alone must never justify customer advice.
            for line in source.get("text", "").splitlines()[3:]:
                if line.casefold().startswith("escalate"):
                    continue
                overlap = words & (set(WORD.findall(line.casefold())) - STOP_WORDS)
                if len(overlap) >= 2 and len(overlap) / max(len(words), 1) >= 0.35:
                    supported.append(source_id)
                    excerpt = line[:300]
                    break
        if supported:
            approved.append({"text": excerpt, "citations": supported, "supporting_excerpt": excerpt,
                             "selected_from_model_step": step["text"]})
    return approved


class PortalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('customer','admin')),
                    created_at TEXT NOT NULL, failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, complaint TEXT NOT NULL,
                    product_hint TEXT, status TEXT NOT NULL DEFAULT 'open',
                    analysis_status TEXT NOT NULL DEFAULT 'pending', analysis_json TEXT,
                    customer_steps_json TEXT NOT NULL DEFAULT '[]', analysis_error TEXT,
                    public_note TEXT NOT NULL DEFAULT '', admin_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS tickets_owner ON tickets(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS tickets_status ON tickets(status, created_at DESC);
                CREATE TABLE IF NOT EXISTS ticket_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL,
                    trace_id TEXT, decision TEXT, result_json TEXT, error TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    ticket_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK(rating IN ('helpful','not_helpful')),
                    comment TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            con.commit()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def create_user(self, email: str, password: str, role: str = "customer") -> dict:
        email = email.strip().casefold()
        if role not in {"customer", "admin"} or len(password) < 12 or len(password) > 128:
            raise ValueError("Role or password is invalid; use 12–128 password characters")
        if len(email) > 254 or not EMAIL.fullmatch(email):
            raise ValueError("Enter a valid email address")
        user = {"id": str(uuid.uuid4()), "email": email, "role": role}
        try:
            with closing(self.connect()) as con:
                con.execute("INSERT INTO users(id,email,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                            (user["id"], email, password_hash(password), role, utc_now()))
                con.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Account already exists") from exc
        return user

    def authenticate(self, email: str, password: str) -> dict | None:
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM users WHERE email=?", (email.strip().casefold(),)).fetchone()
            if not row:
                return None
            if row["locked_until"] > int(time.time()):
                return None
            if not password_matches(password, row["password_hash"]):
                failures = row["failed_attempts"] + 1
                lock = int(time.time()) + 600 if failures >= 5 else 0
                con.execute("UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?",
                            (failures, lock, row["id"]))
                con.commit()
                return None
            con.execute("UPDATE users SET failed_attempts=0,locked_until=0 WHERE id=?", (row["id"],))
            con.commit()
            return {"id": row["id"], "email": row["email"], "role": row["role"]}

    def new_session(self, user_id: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        with closing(self.connect()) as con:
            con.execute("DELETE FROM sessions WHERE expires_at<?", (int(time.time()),))
            con.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                        (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, int(time.time()) + SESSION_SECONDS))
            con.commit()
        return token, csrf

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with closing(self.connect()) as con:
            row = con.execute("""SELECT u.id,u.email,u.role,s.csrf_token FROM sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?""",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        return dict(row) if row else None

    def revoke(self, token: str) -> None:
        with closing(self.connect()) as con:
            con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            con.commit()

    def create_ticket(self, owner_id: str, complaint: str, product_hint: str | None) -> str:
        ticket_id, now = f"CASE-{uuid.uuid4().hex[:12].upper()}", utc_now()
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO tickets(id,owner_id,complaint,product_hint,created_at,updated_at)
                VALUES (?,?,?,?,?,?)""", (ticket_id, owner_id, complaint, product_hint, now, now))
            con.execute("INSERT INTO ticket_events(ticket_id,actor_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                        (ticket_id, owner_id, "created", "Customer submitted ticket", now))
            con.commit()
        return ticket_id

    def set_analysis(self, ticket_id: str, analysis: dict | None, error: str = "") -> None:
        now = utc_now()
        steps = customer_steps(analysis) if analysis else []
        degraded = analysis is None or any("unavailable" in warning.casefold()
                                           for warning in analysis.get("warnings", []))
        with closing(self.connect()) as con:
            con.execute("""UPDATE tickets SET analysis_status=?,analysis_json=?,customer_steps_json=?,
                analysis_error=?,updated_at=? WHERE id=?""",
                ("needs_review" if degraded else "ready", json.dumps(analysis, ensure_ascii=False) if analysis else None,
                 json.dumps(steps, ensure_ascii=False), error[:300], now, ticket_id))
            con.execute("INSERT INTO ticket_events(ticket_id,actor_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                        (ticket_id, "system", "analyzed" if analysis else "analysis_failed",
                         "Customer steps released" if steps else "Admin review required", now))
            con.execute("""INSERT INTO analysis_runs(ticket_id,trace_id,decision,result_json,error,created_at)
                VALUES (?,?,?,?,?,?)""", (ticket_id, analysis.get("trace_id") if analysis else None,
                analysis.get("decision") if analysis else None,
                json.dumps(analysis, ensure_ascii=False) if analysis else None, error[:300], now))
            con.commit()

    def get_ticket(self, ticket_id: str) -> dict | None:
        with closing(self.connect()) as con:
            row = con.execute("""SELECT t.*,u.email AS customer_email FROM tickets t JOIN users u ON u.id=t.owner_id
                WHERE t.id=?""", (ticket_id,)).fetchone()
            if not row:
                return None
            events = [dict(event) for event in con.execute(
                "SELECT actor_id,action,detail,created_at FROM ticket_events WHERE ticket_id=? ORDER BY id",
                (ticket_id,)).fetchall()]
            feedback = con.execute("SELECT rating,comment,created_at FROM feedback WHERE ticket_id=?", (ticket_id,)).fetchone()
            runs = con.execute("""SELECT trace_id,decision,result_json,error,created_at FROM analysis_runs
                WHERE ticket_id=? ORDER BY id DESC LIMIT 20""", (ticket_id,)).fetchall()
        result = dict(row)
        result["analysis"] = json.loads(result.pop("analysis_json")) if result["analysis_json"] else None
        result["suggested_steps"] = json.loads(result.pop("customer_steps_json"))
        result["events"] = events
        result["feedback"] = dict(feedback) if feedback else None
        result["analysis_runs"] = [{"trace_id": run["trace_id"], "decision": run["decision"],
                                   "result": json.loads(run["result_json"]) if run["result_json"] else None,
                                   "error": run["error"], "created_at": run["created_at"]} for run in runs]
        return result

    def list_tickets(self, owner_id: str | None = None) -> list[dict]:
        with closing(self.connect()) as con:
            sql = "SELECT id FROM tickets " + ("WHERE owner_id=? " if owner_id else "") + "ORDER BY created_at DESC"
            rows = con.execute(sql, (owner_id,) if owner_id else ()).fetchall()
        return [self.get_ticket(row["id"]) for row in rows]

    def update_status(self, ticket_id: str, actor_id: str, status: str, public_note: str, admin_note: str) -> None:
        now = utc_now()
        with closing(self.connect()) as con:
            result = con.execute("""UPDATE tickets SET status=?,public_note=?,admin_note=?,updated_at=? WHERE id=?""",
                (status, public_note, admin_note, now, ticket_id))
            if not result.rowcount:
                raise KeyError(ticket_id)
            con.execute("INSERT INTO ticket_events(ticket_id,actor_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                        (ticket_id, actor_id, "status_changed", json.dumps({
                            "status": status, "public_note": public_note, "admin_note": admin_note
                        }, ensure_ascii=False), now))
            con.commit()

    def save_feedback(self, ticket_id: str, user_id: str, rating: str, comment: str) -> None:
        with closing(self.connect()) as con:
            con.execute("""INSERT INTO feedback(ticket_id,user_id,rating,comment,created_at) VALUES (?,?,?,?,?)
                ON CONFLICT(ticket_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,
                created_at=excluded.created_at""", (ticket_id, user_id, rating, comment, utc_now()))
            con.commit()

    def metrics(self) -> dict:
        with closing(self.connect()) as con:
            rows = con.execute("SELECT status,analysis_status,analysis_json FROM tickets ORDER BY created_at DESC LIMIT 100").fetchall()
            feedback = con.execute("SELECT rating,COUNT(*) FROM feedback GROUP BY rating").fetchall()
        status = Counter(row["status"] for row in rows)
        analysis_status = Counter(row["analysis_status"] for row in rows)
        ready = [json.loads(row["analysis_json"]) for row in rows if row["analysis_json"]]
        unknown = sum(item.get("triage", {}).get("intent") == "other" for item in ready)
        abstained = sum(item.get("decision") == "insufficient_evidence" for item in ready)
        unknown_rate = round(unknown / len(ready), 3) if ready else 0
        abstain_rate = round(abstained / len(ready), 3) if ready else 0
        return {"recent_ticket_count": len(rows), "status_counts": dict(status),
                "analysis_counts": dict(analysis_status), "feedback_counts": dict(feedback),
                "unknown_intent_rate": unknown_rate, "abstention_rate": abstain_rate,
                "drift_signal": len(ready) >= 20 and (unknown_rate >= 0.25 or abstain_rate >= 0.5),
                "note": "Heuristic signal on the latest 100 tickets; not a validated drift detector."}


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class TicketInput(BaseModel):
    complaint: str = Field(min_length=5, max_length=5000)
    product_hint: str | None = Field(default=None, max_length=100)


class StatusInput(BaseModel):
    status: Literal["open", "needs_information", "in_progress", "resolved", "closed"]
    public_note: str = Field(default="", max_length=1000)
    admin_note: str = Field(default="", max_length=2000)


class FeedbackInput(BaseModel):
    rating: Literal["helpful", "not_helpful"]
    comment: str = Field(default="", max_length=1000)


def customer_view(ticket: dict) -> dict:
    analysis = ticket.get("analysis") or {}
    return {key: ticket[key] for key in (
        "id", "complaint", "product_hint", "status", "analysis_status", "created_at", "updated_at",
        "public_note", "suggested_steps", "feedback"
    )} | {"triage": analysis.get("triage"), "summary": analysis.get("summary") if ticket["suggested_steps"] else
          "Your ticket is being reviewed by support."}


def install_portal(app: FastAPI, settings: Settings,
                   analyzer: Callable[[ResolveInput], Awaitable[dict]]) -> None:
    store = PortalStore(settings.portal_db)
    app.state.portal_store = store

    def session_user(request: Request) -> dict:
        user = store.session(request.cookies.get("telecom_session"))
        if not user:
            raise HTTPException(401, "Sign in required")
        return user

    def customer(user: dict = Depends(session_user)) -> dict:
        if user["role"] != "customer":
            raise HTTPException(403, "Customer role required")
        return user

    def admin(user: dict = Depends(session_user)) -> dict:
        if user["role"] != "admin":
            raise HTTPException(403, "Admin role required")
        return user

    def check_csrf(user: dict = Depends(session_user), x_csrf_token: str | None = Header(default=None)) -> dict:
        if not x_csrf_token or not hmac.compare_digest(user["csrf_token"], x_csrf_token):
            raise HTTPException(403, "CSRF token required")
        return user

    def customer_write(user: dict = Depends(check_csrf)) -> dict:
        if user["role"] != "customer":
            raise HTTPException(403, "Customer role required")
        return user

    def admin_write(user: dict = Depends(check_csrf)) -> dict:
        if user["role"] != "admin":
            raise HTTPException(403, "Admin role required")
        return user

    def set_cookie(response: Response, token: str, request: Request) -> None:
        response.set_cookie("telecom_session", token, max_age=SESSION_SECONDS, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https", path="/")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/login")

    @app.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(WEB / "login.html")

    @app.get("/portal.css", include_in_schema=False)
    def portal_styles() -> FileResponse:
        return FileResponse(WEB / "portal.css", media_type="text/css")

    @app.get("/customer", include_in_schema=False)
    def customer_page(user: dict = Depends(customer)) -> FileResponse:
        return FileResponse(WEB / "customer.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page(user: dict = Depends(admin)) -> FileResponse:
        return FileResponse(WEB / "admin.html")

    @app.post("/auth/register")
    def register(credentials: Credentials, request: Request, response: Response) -> dict:
        try:
            user = store.create_user(credentials.email, credentials.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        token, csrf = store.new_session(user["id"])
        set_cookie(response, token, request)
        return {**user, "csrf_token": csrf}

    @app.post("/auth/login")
    def login(credentials: Credentials, request: Request, response: Response) -> dict:
        user = store.authenticate(credentials.email, credentials.password)
        if not user:
            raise HTTPException(401, "Invalid credentials or temporarily locked account")
        token, csrf = store.new_session(user["id"])
        set_cookie(response, token, request)
        return {**user, "csrf_token": csrf}

    @app.get("/auth/me")
    def me(user: dict = Depends(session_user)) -> dict:
        return user

    @app.post("/auth/logout")
    def logout(request: Request, response: Response, user: dict = Depends(check_csrf)) -> dict:
        store.revoke(request.cookies.get("telecom_session", ""))
        response.delete_cookie("telecom_session", path="/")
        return {"signed_out": True}

    async def run_analysis(ticket_id: str, complaint: str, product_hint: str | None) -> None:
        try:
            result = await analyzer(ResolveInput(complaint=redact_for_model(complaint), product_hint=product_hint))
            store.set_analysis(ticket_id, result)
        except Exception as exc:  # noqa: BLE001  Ticket submission must survive unavailable local models/services.
            store.set_analysis(ticket_id, None, f"Analysis unavailable: {type(exc).__name__}")

    @app.post("/v1/tickets", status_code=201)
    async def create_ticket(data: TicketInput, user: dict = Depends(customer_write)) -> dict:
        ticket_id = store.create_ticket(user["id"], data.complaint, data.product_hint)
        await run_analysis(ticket_id, data.complaint, data.product_hint)
        return customer_view(store.get_ticket(ticket_id))

    @app.get("/v1/tickets")
    def my_tickets(user: dict = Depends(customer)) -> dict:
        return {"tickets": [customer_view(item) for item in store.list_tickets(user["id"])]}

    @app.get("/v1/tickets/{ticket_id}")
    def my_ticket(ticket_id: str, user: dict = Depends(customer)) -> dict:
        ticket = store.get_ticket(ticket_id)
        if not ticket or ticket["owner_id"] != user["id"]:
            raise HTTPException(404, "Ticket not found")
        return customer_view(ticket)

    @app.post("/v1/tickets/{ticket_id}/feedback")
    def feedback(ticket_id: str, data: FeedbackInput, user: dict = Depends(customer_write)) -> dict:
        ticket = store.get_ticket(ticket_id)
        if not ticket or ticket["owner_id"] != user["id"]:
            raise HTTPException(404, "Ticket not found")
        store.save_feedback(ticket_id, user["id"], data.rating, data.comment)
        return customer_view(store.get_ticket(ticket_id))

    @app.get("/v1/admin/tickets")
    def all_tickets(user: dict = Depends(admin)) -> dict:
        return {"tickets": store.list_tickets()}

    @app.get("/v1/admin/tickets/{ticket_id}")
    def full_ticket(ticket_id: str, user: dict = Depends(admin)) -> dict:
        ticket = store.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(404, "Ticket not found")
        return ticket

    @app.post("/v1/admin/tickets/{ticket_id}/status")
    def change_status(ticket_id: str, data: StatusInput, user: dict = Depends(admin_write)) -> dict:
        if data.status in {"resolved", "closed"} and not data.admin_note.strip():
            raise HTTPException(422, "A resolution or closure note is required")
        try:
            store.update_status(ticket_id, user["id"], data.status, data.public_note, data.admin_note)
        except KeyError as exc:
            raise HTTPException(404, "Ticket not found") from exc
        return store.get_ticket(ticket_id)

    @app.post("/v1/admin/tickets/{ticket_id}/analyze")
    async def retry_analysis(ticket_id: str, user: dict = Depends(admin_write)) -> dict:
        ticket = store.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(404, "Ticket not found")
        await run_analysis(ticket_id, ticket["complaint"], ticket["product_hint"])
        return store.get_ticket(ticket_id)

    @app.get("/v1/admin/metrics")
    def metrics(user: dict = Depends(admin)) -> dict:
        return store.metrics()

    @app.get("/v1/admin/system")
    async def system_status(user: dict = Depends(admin)) -> dict:
        result = {"ollama": "unavailable", "chat_model": settings.chat_model,
                  "embedding_model": settings.embed_model, "models_present": [],
                  "knowledge": "unavailable"}
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                response = await client.get(f"{settings.ollama_url}/api/tags")
                response.raise_for_status()
                names = [item["name"] for item in response.json().get("models", [])]
                result["models_present"] = names
                if any(name == settings.chat_model for name in names) and any(
                    name == settings.embed_model or name.startswith(settings.embed_model + ":") for name in names
                ):
                    result["ollama"] = "ready"
                else:
                    result["ollama"] = "models_missing"
            except (httpx.HTTPError, KeyError, ValueError):
                pass
            try:
                response = await client.get(f"{settings.knowledge_url}/health",
                                            headers={"X-Service-Token": settings.service_token()})
                response.raise_for_status()
                result["knowledge"] = "ready"
                result["knowledge_counts"] = response.json().get("counts", {})
            except (httpx.HTTPError, KeyError, ValueError):
                pass
        return result

    @app.get("/v1/admin/taxonomy")
    async def taxonomy(user: dict = Depends(admin)) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{settings.knowledge_url}/v1/taxonomy",
                                            headers={"X-Service-Token": settings.service_token()})
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Knowledge service unavailable") from exc

    @app.post("/v1/admin/taxonomy")
    async def add_taxonomy(data: dict, user: dict = Depends(admin_write)) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(f"{settings.knowledge_url}/v1/taxonomy", json=data,
                                             headers={"X-Service-Token": settings.service_token()})
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Knowledge service unavailable") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local portal accounts")
    parser.add_argument("action", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("New admin password (12+ characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    user = PortalStore(Settings.from_env().portal_db).create_user(args.email, password, "admin")
    print(f"Admin created: {user['email']}")


if __name__ == "__main__":
    main()
