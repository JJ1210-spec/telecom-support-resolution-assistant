"""Relational schema (SQLAlchemy Core). Neon Postgres in hosted mode, SQLite for tests/offline.

The relational store is the system of record: tickets, conversations, step outcomes, taxonomy
versions, KB versions and traces. Vector indexes are derived data and can
be rebuilt from here (embeddings are cached by content hash, so a rebuild costs no API tokens).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import numpy as np
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

metadata = sa.MetaData()


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def vec_to_bytes(vector: list[float] | np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float16).tobytes()


def bytes_to_vec(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


TS = sa.DateTime(timezone=True)

users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("email", sa.String(254), nullable=False, unique=True),
    sa.Column("name", sa.String(120), nullable=False, server_default=""),
    sa.Column("password_hash", sa.String(255), nullable=False),
    sa.Column("role", sa.String(16), nullable=False),  # customer | admin
    sa.Column("region", sa.String(80), nullable=True),
    sa.Column("failed_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("locked_until", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", TS, nullable=False),
)

sessions = sa.Table(
    "sessions", metadata,
    sa.Column("token_hash", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("csrf_token", sa.String(64), nullable=False),
    sa.Column("expires_at", sa.Integer, nullable=False),
)

tickets = sa.Table(
    "tickets", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("owner_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("subject", sa.String(200), nullable=False, server_default=""),
    sa.Column("complaint", sa.Text, nullable=False),
    sa.Column("complaint_redacted", sa.Text, nullable=False),
    sa.Column("product_hint", sa.String(100), nullable=True),
    sa.Column("region", sa.String(80), nullable=True),
    sa.Column("language", sa.String(16), nullable=True),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("route", sa.String(24), nullable=True),  # self_service | assisted | human
    sa.Column("intent", sa.String(100), nullable=True, index=True),
    sa.Column("category", sa.String(100), nullable=True),
    sa.Column("product", sa.String(100), nullable=True),
    sa.Column("severity", sa.String(8), nullable=True),
    sa.Column("sentiment", sa.String(24), nullable=True),
    sa.Column("confidence", sa.Float, nullable=True),
    sa.Column("assignee_id", sa.String(40), nullable=True),
    sa.Column("incident_id", sa.String(32), nullable=True, index=True),
    sa.Column("reopen_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("intake", sa.JSON, nullable=True),
    sa.Column("triage", sa.JSON, nullable=True),
    sa.Column("sources", sa.JSON, nullable=True),
    sa.Column("decision", sa.JSON, nullable=True),
    sa.Column("copilot", sa.JSON, nullable=True),
    sa.Column("resolution_summary", sa.JSON, nullable=True),
    sa.Column("embedding", sa.LargeBinary, nullable=True),
    sa.Column("top_similarity", sa.Float, nullable=True),
    sa.Column("trace_id", sa.String(40), nullable=True),
    sa.Column("analysis_state", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("sla_due_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False, index=True),
    sa.Column("updated_at", TS, nullable=False),
    sa.Column("resolved_at", TS, nullable=True),
)

ticket_events = sa.Table(
    "ticket_events", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ticket_id", sa.String(32), nullable=False, index=True),
    sa.Column("actor_id", sa.String(40), nullable=False),
    sa.Column("actor_role", sa.String(16), nullable=False),
    sa.Column("kind", sa.String(40), nullable=False),
    sa.Column("detail", sa.JSON, nullable=True),
    sa.Column("created_at", TS, nullable=False),
)

messages = sa.Table(
    "messages", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("ticket_id", sa.String(32), nullable=False, index=True),
    sa.Column("author_id", sa.String(40), nullable=False),
    sa.Column("author_role", sa.String(16), nullable=False),  # customer | admin | ai | system
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("options", sa.JSON, nullable=True),  # quick-reply choices
    sa.Column("answered_option", sa.String(200), nullable=True),
    sa.Column("step_id", sa.String(40), nullable=True, index=True),  # step-scoped side chat
    sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),  # public | internal
    sa.Column("created_at", TS, nullable=False),
)

steps = sa.Table(
    "steps", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("ticket_id", sa.String(32), nullable=False, index=True),
    sa.Column("origin", sa.String(16), nullable=False),  # ai | admin
    sa.Column("plan_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=True),
    sa.Column("citations", sa.JSON, nullable=True),
    sa.Column("customer_visible", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),  # pending|worked|did_not_work
    sa.Column("status_note", sa.Text, nullable=True),
    sa.Column("status_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
)

intake_sessions = sa.Table(
    "intake_sessions", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("user_id", sa.String(40), nullable=False, index=True),
    sa.Column("state", sa.JSON, nullable=False),
    sa.Column("ticket_id", sa.String(32), nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

feedback = sa.Table(
    "feedback", metadata,
    sa.Column("ticket_id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.String(40), nullable=False),
    sa.Column("rating", sa.Integer, nullable=False),  # 1..5 CSAT
    sa.Column("comment", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", TS, nullable=False),
)

kb_articles = sa.Table(
    "kb_articles", metadata,
    sa.Column("kb_id", sa.String(60), primary_key=True),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("product", sa.String(100), nullable=True),
    sa.Column("intent", sa.String(100), nullable=True),
    sa.Column("summary", sa.Text, nullable=False, server_default=""),
    sa.Column("checks", sa.JSON, nullable=False),
    sa.Column("self_help", sa.JSON, nullable=False),
    sa.Column("escalation", sa.Text, nullable=False, server_default=""),
    sa.Column("status", sa.String(16), nullable=False),  # draft | published | deprecated | rejected
    sa.Column("origin", sa.String(16), nullable=False, server_default="seed"),  # seed | learned | manual
    sa.Column("source_ticket_id", sa.String(32), nullable=True),
    sa.Column("review_reason", sa.Text, nullable=True),
    sa.Column("created_by", sa.String(40), nullable=True),
    sa.Column("updated_at", TS, nullable=False),
)

corpus_tickets = sa.Table(
    "corpus_tickets", metadata,
    sa.Column("ticket_id", sa.String(40), primary_key=True),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("status", sa.String(16), nullable=False),  # resolved | unresolved
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("intent", sa.String(100), nullable=True, index=True),
    sa.Column("source", sa.String(40), nullable=False),  # synthetic | learned
    sa.Column("outcome_score", sa.Float, nullable=False, server_default="0.5"),
    sa.Column("indexed_at", TS, nullable=True),
)

taxonomy = sa.Table(
    "taxonomy", metadata,
    sa.Column("intent", sa.String(100), primary_key=True),
    sa.Column("label", sa.String(120), nullable=False),
    sa.Column("category", sa.String(100), nullable=False),
    sa.Column("product", sa.String(100), nullable=False),
    sa.Column("area", sa.String(60), nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("sensitive", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    sa.Column("version_added", sa.Integer, nullable=False, server_default="1"),
)

taxonomy_versions = sa.Table(
    "taxonomy_versions", metadata,
    sa.Column("version", sa.Integer, primary_key=True),
    sa.Column("created_by", sa.String(40), nullable=False),
    sa.Column("changelog", sa.JSON, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

taxonomy_proposals = sa.Table(
    "taxonomy_proposals", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("intent", sa.String(100), nullable=False),
    sa.Column("label", sa.String(120), nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("product", sa.String(100), nullable=False),
    sa.Column("category", sa.String(100), nullable=False),
    sa.Column("area", sa.String(60), nullable=False),
    sa.Column("example_ticket_ids", sa.JSON, nullable=False),
    sa.Column("examples", sa.JSON, nullable=False),
    sa.Column("nearest_intent", sa.String(100), nullable=True),
    sa.Column("nearest_similarity", sa.Float, nullable=True),
    sa.Column("cluster_size", sa.Integer, nullable=False),
    sa.Column("cohesion", sa.Float, nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("decided_by", sa.String(40), nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("decided_at", TS, nullable=True),
)

discovery_pool = sa.Table(
    "discovery_pool", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ticket_id", sa.String(32), nullable=False, unique=True),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("reason", sa.String(60), nullable=False),
    sa.Column("embedding", sa.LargeBinary, nullable=True),
    sa.Column("proposal_id", sa.String(40), nullable=True),
    sa.Column("created_at", TS, nullable=False),
)

traces = sa.Table(
    "traces", metadata,
    sa.Column("trace_id", sa.String(40), primary_key=True),
    sa.Column("ticket_id", sa.String(32), nullable=True, index=True),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("data", sa.JSON, nullable=False),
    sa.Column("latency_ms", sa.JSON, nullable=False),
    sa.Column("models", sa.JSON, nullable=False),
    sa.Column("degraded", sa.JSON, nullable=False),
    sa.Column("created_at", TS, nullable=False, index=True),
)

embedding_cache = sa.Table(
    "embedding_cache", metadata,
    sa.Column("content_hash", sa.String(64), primary_key=True),
    sa.Column("model", sa.String(80), primary_key=True),
    sa.Column("vector", sa.LargeBinary, nullable=False),
)

incidents = sa.Table(
    "incidents", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("intent", sa.String(100), nullable=True),
    sa.Column("product", sa.String(100), nullable=True),
    sa.Column("region", sa.String(80), nullable=True),
    sa.Column("status", sa.String(16), nullable=False),  # open | resolved
    sa.Column("centroid", sa.LargeBinary, nullable=True),
    sa.Column("public_note", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("resolved_at", TS, nullable=True),
)

eval_runs = sa.Table(
    "eval_runs", metadata,
    sa.Column("run_id", sa.String(40), primary_key=True),
    sa.Column("git_sha", sa.String(40), nullable=True),
    sa.Column("config", sa.JSON, nullable=False),
    sa.Column("metrics", sa.JSON, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

drift_snapshots = sa.Table(
    "drift_snapshots", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("metrics", sa.JSON, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

audit_log = sa.Table(
    "audit_log", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("actor_id", sa.String(40), nullable=False),
    sa.Column("action", sa.String(60), nullable=False),
    sa.Column("entity", sa.String(40), nullable=False),
    sa.Column("entity_id", sa.String(80), nullable=False),
    sa.Column("diff", sa.JSON, nullable=True),
    sa.Column("created_at", TS, nullable=False),
)


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                from sqlalchemy.pool import StaticPool

                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=280)
        self.engine: Engine = sa.create_engine(url, **kwargs)
        self.is_sqlite = url.startswith("sqlite")
        if self.is_sqlite:
            @sa.event.listens_for(self.engine, "connect")
            def _pragma(dbapi_connection, _record):  # pragma: no cover - driver hook
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    def create_all(self) -> None:
        metadata.create_all(self.engine)
        # Remove tables from installations that used the retired delivery subsystem.
        with self.engine.begin() as con:
            con.execute(sa.text("DROP TABLE IF EXISTS outbox"))
            con.execute(sa.text("DROP TABLE IF EXISTS email_log"))

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        with self.engine.begin() as con:
            yield con

    @contextmanager
    def read(self) -> Iterator[Connection]:
        with self.engine.connect() as con:
            yield con

    def ping(self) -> bool:
        try:
            with self.read() as con:
                con.execute(sa.text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False


def row_dict(row) -> dict:
    data = dict(row._mapping)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = iso(value)
    return data
