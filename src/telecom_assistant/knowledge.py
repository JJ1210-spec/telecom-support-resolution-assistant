"""Knowledge service: canonical records, versioned upserts, and semantic search."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .ollama import OllamaClient, OllamaError


class RecordInput(BaseModel):
    kind: Literal["ticket", "kb"]
    payload: dict


class SearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=5000)
    pool: Literal["evidence", "unresolved"] = "evidence"
    kind: Literal["ticket", "kb"] | None = None
    limit: int = Field(default=5, ge=1, le=20)


class TaxonomyInput(BaseModel):
    intent: str = Field(min_length=3, max_length=100)
    category: str = Field(min_length=2, max_length=100)
    product: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)


def document_text(kind: str, payload: dict) -> str:
    if kind == "ticket":
        return "\n".join(
            str(part) for part in [
                payload.get("subject") or "",
                payload.get("body") or "",
                payload.get("product") or "",
                payload.get("resolution_summary") or "",
                "\n".join(payload.get("resolution_steps") or []),
            ] if part
        )[:6000]
    return "\n".join(
        str(part) for part in [
            payload.get("title") or "",
            payload.get("product") or "",
            payload.get("summary") or "",
            "\n".join(payload.get("checks") or []),
            payload.get("escalation_criteria") or "",
        ] if part
    )[:6000]


def record_identity(kind: str, payload: dict) -> tuple[str, str, int]:
    key = "ticket_id" if kind == "ticket" else "kb_id"
    version_key = "record_version" if kind == "ticket" else "article_version"
    record_id = payload.get(key)
    version = payload.get(version_key)
    status = payload.get("status")
    allowed = {"resolved", "unresolved"} if kind == "ticket" else {"published", "deprecated"}
    if not isinstance(record_id, str) or not record_id or not isinstance(version, int) or version < 1:
        raise ValueError(f"{kind} requires a stable ID and positive {version_key}")
    if status not in allowed:
        raise ValueError(f"Invalid {kind} status: {status}")
    if kind == "ticket" and status == "resolved" and (not payload.get("resolution_steps") or not payload.get("closure_evidence")):
        raise ValueError("Resolved tickets require resolution_steps and closure_evidence")
    if kind == "ticket" and status == "unresolved" and (payload.get("resolution_steps") or payload.get("closure_evidence")):
        raise ValueError("Unresolved tickets cannot contain completed resolution evidence")
    if not document_text(kind, payload).strip():
        raise ValueError("Cannot index an empty record")
    return record_id, status, version


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return -1.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return -1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class KnowledgeStore:
    def __init__(self, path: Path, embedding_model: str) -> None:
        self.path = path
        self.embedding_model = embedding_model
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS records (
                    record_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_pool ON records(kind, status, embedding_model);
                CREATE TABLE IF NOT EXISTS taxonomy (
                    intent TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    product TEXT NOT NULL,
                    description TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            con.commit()

    def upsert(self, kind: str, payload: dict, embedding: list[float]) -> str:
        record_id, status, version = record_identity(kind, payload)
        if not embedding or not all(math.isfinite(x) for x in embedding):
            raise ValueError("Embedding must contain finite values")
        source_text = document_text(kind, payload)
        content_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.path, timeout=30)) as con:
            old = con.execute(
                "SELECT version, content_hash, status, embedding_model FROM records WHERE record_id=?", (record_id,)
            ).fetchone()
            if old:
                if version < old[0]:
                    raise ValueError(f"Stale version for {record_id}: {version} < {old[0]}")
                if version == old[0]:
                    if (content_hash, status, self.embedding_model) == (old[1], old[2], old[3]):
                        return "unchanged"
                    raise ValueError(f"Conflicting content or status at version {version} for {record_id}")
            con.execute(
                """INSERT INTO records(record_id,kind,status,version,content_hash,embedding_model,embedding_json,payload_json)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(record_id) DO UPDATE SET kind=excluded.kind,status=excluded.status,
                version=excluded.version,content_hash=excluded.content_hash,
                embedding_model=excluded.embedding_model,embedding_json=excluded.embedding_json,
                payload_json=excluded.payload_json""",
                (record_id, kind, status, version, content_hash, self.embedding_model,
                 json.dumps(embedding), json.dumps(payload, ensure_ascii=False)),
            )
            con.commit()
        return "updated" if old else "inserted"

    def search(self, embedding: list[float], pool: str, kind: str | None, limit: int) -> list[dict]:
        if pool == "unresolved" and kind == "kb":
            return []
        statuses = ("resolved", "published") if pool == "evidence" else ("unresolved",)
        placeholders = ",".join("?" for _ in statuses)
        sql = f"SELECT record_id,kind,status,embedding_json,payload_json FROM records WHERE status IN ({placeholders}) AND embedding_model=?"
        args: list = [*statuses, self.embedding_model]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(sql, args).fetchall()
        ranked = []
        for record_id, record_kind, status, vector_json, payload_json in rows:
            score = cosine(embedding, json.loads(vector_json))
            if score < -0.5:
                continue
            payload = json.loads(payload_json)
            ranked.append({
                "source_id": record_id, "kind": record_kind, "status": status,
                "score": round(score, 5), "text": document_text(record_kind, payload),
                "payload": payload,
            })
        ranked.sort(key=lambda item: (-item["score"], item["source_id"]))
        return ranked[:limit]

    def upsert_taxonomy(self, data: TaxonomyInput) -> int:
        with closing(sqlite3.connect(self.path, timeout=30)) as con:
            old = con.execute("SELECT version FROM taxonomy WHERE intent=?", (data.intent,)).fetchone()
            version = old[0] + 1 if old else 1
            con.execute(
                """INSERT INTO taxonomy(intent,category,product,description,version,active)
                VALUES (?,?,?,?,?,1) ON CONFLICT(intent) DO UPDATE SET category=excluded.category,
                product=excluded.product,description=excluded.description,version=excluded.version,active=1""",
                (data.intent, data.category, data.product, data.description, version),
            )
            con.commit()
        return version

    def taxonomy(self) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT intent,category,product,description,version FROM taxonomy WHERE active=1 ORDER BY intent"
            ).fetchall()
        return [dict(zip(("intent", "category", "product", "description", "version"), row)) for row in rows]

    def get_record(self, record_id: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute("SELECT payload_json FROM records WHERE record_id=?", (record_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def counts(self) -> dict:
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute("SELECT kind,status,COUNT(*) FROM records GROUP BY kind,status").fetchall()
        return {f"{kind}_{status}": count for kind, status, count in rows}


def create_app(settings: Settings | None = None, embedder: OllamaClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    embedder = embedder or OllamaClient(settings.ollama_url, settings.embed_model, settings.chat_model)
    store = KnowledgeStore(settings.knowledge_db, settings.embed_model)
    app = FastAPI(title="Telecom Knowledge Service", version="0.1.0")
    app.state.store = store

    def require_service(x_service_token: str | None = Header(default=None)) -> None:
        if not x_service_token or not secrets.compare_digest(x_service_token, settings.service_token()):
            raise HTTPException(401, "Service authentication required")

    @app.get("/health", dependencies=[Depends(require_service)])
    def health() -> dict:
        return {"status": "ok", "counts": store.counts(), "embedding_model": settings.embed_model}

    @app.get("/v1/taxonomy", dependencies=[Depends(require_service)])
    def get_taxonomy() -> dict:
        return {"classes": store.taxonomy()}

    @app.post("/v1/taxonomy", dependencies=[Depends(require_service)])
    def put_taxonomy(data: TaxonomyInput) -> dict:
        return {"version": store.upsert_taxonomy(data)}

    @app.post("/v1/records", dependencies=[Depends(require_service)])
    async def put_record(record: RecordInput) -> dict:
        try:
            record_id, _, _ = record_identity(record.kind, record.payload)
            vector = (await embedder.embed_many([document_text(record.kind, record.payload)]))[0]
            action = store.upsert(record.kind, record.payload, vector)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OllamaError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"record_id": record_id, "action": action}

    @app.post("/v1/search", dependencies=[Depends(require_service)])
    async def search(request: SearchInput) -> dict:
        try:
            vector = (await embedder.embed_many([request.query]))[0]
        except OllamaError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"results": store.search(vector, request.pool, request.kind, request.limit), "pool": request.pool}

    return app


app = create_app()
