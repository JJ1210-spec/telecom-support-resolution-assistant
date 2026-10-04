"""One ingestion path for bulk seed loads, live resolved tickets, learned resolutions and KB edits.

Idempotency: every record carries a version and a content hash. Older versions are rejected, the
same version with the same content is a no-op, and a status-only change (KB deprecation, ticket
reopen) is a payload update that takes effect in the index immediately without re-embedding.
KB articles are chunked per section so citations point at the exact check: ``KB-BB-DROP#c2``.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import sqlalchemy as sa

from ..db import Database, corpus_tickets, kb_articles, utc_now
from ..gateways.embeddings import EmbeddingError, HashEmbedder, JinaEmbedder
from ..gateways.vectors import LocalIndex, Point, QdrantIndex, bm25_doc
from ..telemetry import log_event, metrics

TICKETS, KB = "tickets", "kb"


class StaleVersion(ValueError):
    pass


def ticket_text(payload: dict) -> str:
    parts = [
        payload.get("subject") or "",
        payload.get("body") or "",
        f"Product: {payload.get('product') or ''}",
        f"Root cause: {payload.get('resolution_summary') or ''}",
        "Resolution: " + "; ".join(payload.get("resolution_steps") or []),
    ]
    return "\n".join(p for p in parts if p.strip())[:6000]


def kb_chunks(article: dict) -> list[dict]:
    title = article["title"]
    base = {"kind": "kb", "kb_id": article["kb_id"], "title": title, "product": article.get("product"),
            "intent": article.get("intent"), "status": article["status"], "version": article["version"],
            "origin": article.get("origin", "seed")}
    chunks = [{**base, "source_id": f"{article['kb_id']}#summary", "section": "summary", "audience": "admin",
               "text": f"{title}. {article.get('summary', '')}"}]
    for index, step in enumerate(article.get("self_help") or [], 1):
        chunks.append({**base, "source_id": f"{article['kb_id']}#h{index}", "section": f"self-help {index}",
                       "audience": "customer", "text": f"{title} — customer self-help {index}: {step}"})
    for index, check in enumerate(article.get("checks") or [], 1):
        chunks.append({**base, "source_id": f"{article['kb_id']}#c{index}", "section": f"admin check {index}",
                       "audience": "admin", "text": f"{title} — admin check {index}: {check}"})
    if article.get("escalation"):
        chunks.append({**base, "source_id": f"{article['kb_id']}#escalation", "section": "escalation",
                       "audience": "admin", "text": f"{title} — escalation: {article['escalation']}"})
    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class IndexReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    payload_only: int = 0
    stored_not_indexed: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Indexer:
    def __init__(self, db: Database, index: LocalIndex | QdrantIndex, embedder: JinaEmbedder | HashEmbedder,
                 dim: int) -> None:
        self.db, self.index, self.embedder, self.dim = db, index, embedder, dim

    async def ensure(self) -> None:
        await self.index.ensure(TICKETS, self.dim)
        await self.index.ensure(KB, self.dim)

    async def _embed(self, texts: list[str]):
        try:
            return await self.embedder.embed(texts, task="passage")
        except EmbeddingError:
            log_event("embed_degraded", count=len(texts))
            return [None] * len(texts)

    # ------------------------------------------------------------------ tickets
    async def index_tickets(self, rows: list[dict], source: str = "synthetic") -> IndexReport:
        report = IndexReport()
        to_embed: list[tuple[dict, str, str]] = []
        with self.db.read() as con:
            existing = {r.ticket_id: r for r in con.execute(sa.select(
                corpus_tickets.c.ticket_id, corpus_tickets.c.version, corpus_tickets.c.content_hash,
                corpus_tickets.c.status).where(corpus_tickets.c.ticket_id.in_([r["ticket_id"] for r in rows])))}
        payload_updates: list[dict] = []
        for row in rows:
            version = int(row.get("record_version") or row.get("version") or 1)
            text = ticket_text(row)
            digest = content_hash(text)
            old = existing.get(row["ticket_id"])
            if old and version < old.version:
                raise StaleVersion(f"Stale version for {row['ticket_id']}: {version} < {old.version}")
            if old and old.content_hash == digest and old.status == row["status"]:
                report.unchanged += 1
                continue
            if old and old.content_hash == digest:
                payload_updates.append(row)
                continue
            to_embed.append((row, text, digest))
        vectors = await self._embed([text for _, text, _ in to_embed])
        points: list[Point] = []
        now = utc_now()
        with self.db.tx() as con:
            for (row, text, digest), vector in zip(to_embed, vectors, strict=True):
                version = int(row.get("record_version") or row.get("version") or 1)
                indexable = row["status"] == "resolved" and bool(row.get("resolution_steps"))
                values = {"payload": row, "status": row["status"], "version": version, "content_hash": digest,
                          "intent": row.get("intent"), "source": row.get("source_kind", source),
                          "outcome_score": float(row.get("outcome_score", 0.7 if indexable else 0.5)),
                          "indexed_at": now if indexable else None}
                if row["ticket_id"] in existing:
                    con.execute(corpus_tickets.update().where(corpus_tickets.c.ticket_id == row["ticket_id"])
                                .values(**values))
                    report.updated += 1
                else:
                    con.execute(corpus_tickets.insert().values(ticket_id=row["ticket_id"], **values))
                    report.inserted += 1
                if indexable:
                    points.append(Point(row["ticket_id"], vector, bm25_doc(text), self.ticket_payload(row, text)))
                else:
                    report.stored_not_indexed += 1
            for row in payload_updates:
                con.execute(corpus_tickets.update().where(corpus_tickets.c.ticket_id == row["ticket_id"])
                            .values(status=row["status"], payload=row))
        if points:
            await self.index.upsert(TICKETS, points)
        for row in payload_updates:
            report.payload_only += 1
            if row["status"] != "resolved":
                await self.index.delete(TICKETS, [row["ticket_id"]])
            else:
                await self.index.set_payload(TICKETS, [row["ticket_id"]], {"status": row["status"]})
        metrics.inc("indexed_tickets", float(len(points)))
        return report

    @staticmethod
    def ticket_payload(row: dict, text: str) -> dict:
        return {
            "kind": "ticket", "ticket_id": row["ticket_id"],
            "title": (row.get("subject") or (row.get("body") or "").split(".")[0])[:200],
            "problem": (row.get("body") or "")[:1200], "root_cause": row.get("resolution_summary"),
            "steps": row.get("resolution_steps") or [], "intent": row.get("intent"), "product": row.get("product"),
            "category": row.get("category"), "severity": row.get("severity"), "language": row.get("language"),
            "status": row["status"], "resolved_at": row.get("resolved_at"),
            "outcome_score": float(row.get("outcome_score", 0.7)), "origin": row.get("source_kind", "synthetic"),
            "text": text,
        }

    # ------------------------------------------------------------------ KB
    async def upsert_kb(self, article: dict, actor: str = "system") -> str:
        """Create or version a KB article. Returns inserted | updated | unchanged | status_changed."""
        article = {**article, "version": int(article.get("version") or article.get("article_version") or 1)}
        with self.db.read() as con:
            old = con.execute(sa.select(kb_articles).where(kb_articles.c.kb_id == article["kb_id"])).first()
        fields = {k: article.get(k) for k in ("title", "product", "intent", "summary", "escalation")}
        fields["checks"] = list(article.get("checks") or [])
        fields["self_help"] = list(article.get("self_help") or [])
        if old and article["version"] < old.version:
            raise StaleVersion(f"Stale KB version for {article['kb_id']}")
        same_content = old is not None and all(getattr(old, k) == v for k, v in fields.items())
        now = utc_now()
        values = {**fields, "version": article["version"], "status": article["status"],
                  "origin": article.get("origin", old.origin if old else "seed"),
                  "source_ticket_id": article.get("source_ticket_id"), "created_by": actor, "updated_at": now,
                  "review_reason": article.get("review_reason")}
        with self.db.tx() as con:
            if old:
                con.execute(kb_articles.update().where(kb_articles.c.kb_id == article["kb_id"]).values(**values))
            else:
                con.execute(kb_articles.insert().values(kb_id=article["kb_id"], **values))
        if old and same_content and old.status == article["status"]:
            return "unchanged"
        if article["status"] != "published":
            # Deprecation / draft / rejection is a payload-only change: filtered out of retrieval at once.
            if old:
                old_ids = [c["source_id"] for c in kb_chunks(dict(old._mapping))]
                await self.index.set_payload(KB, old_ids, {"status": article["status"]})
            return "status_changed" if old else "inserted"
        if old:
            await self.index.delete(KB, [c["source_id"] for c in kb_chunks(dict(old._mapping))])
        chunks = kb_chunks({**fields, "kb_id": article["kb_id"], "status": "published",
                            "version": article["version"], "origin": values["origin"]})
        vectors = await self._embed([c["text"] for c in chunks])
        await self.index.upsert(KB, [Point(c["source_id"], v, bm25_doc(c["text"]), c)
                                     for c, v in zip(chunks, vectors, strict=True)])
        metrics.inc("indexed_kb_chunks", float(len(chunks)))
        return "updated" if old else "inserted"

    # ------------------------------------------------------------------ rebuild
    async def rebuild_from_db(self) -> dict:
        """Rebuild the vector index from the system of record (embeddings come from the hash cache)."""
        await self.ensure()
        with self.db.read() as con:
            ticket_rows = [r.payload for r in con.execute(sa.select(corpus_tickets.c.payload).where(
                corpus_tickets.c.status == "resolved"))]
            outcome = {r.ticket_id: r.outcome_score for r in con.execute(sa.select(
                corpus_tickets.c.ticket_id, corpus_tickets.c.outcome_score))}
            articles = [dict(r._mapping) for r in con.execute(sa.select(kb_articles).where(
                kb_articles.c.status == "published"))]
        texts = [ticket_text(r) for r in ticket_rows]
        vectors = await self._embed(texts)
        points = [Point(r["ticket_id"], v, bm25_doc(t),
                        self.ticket_payload({**r, "outcome_score": outcome.get(r["ticket_id"], 0.7)}, t))
                  for r, t, v in zip(ticket_rows, texts, vectors, strict=True)]
        if points:
            await self.index.upsert(TICKETS, points)
        chunks = [c for a in articles for c in kb_chunks(a)]
        kb_vectors = await self._embed([c["text"] for c in chunks])
        if chunks:
            await self.index.upsert(KB, [Point(c["source_id"], v, bm25_doc(c["text"]), c)
                                         for c, v in zip(chunks, kb_vectors, strict=True)])
        return {"tickets": len(points), "kb_chunks": len(chunks)}

    async def set_outcome(self, ticket_id: str, outcome_score: float) -> None:
        with self.db.tx() as con:
            con.execute(corpus_tickets.update().where(corpus_tickets.c.ticket_id == ticket_id)
                        .values(outcome_score=outcome_score))
        await asyncio.gather(self.index.set_payload(TICKETS, [ticket_id], {"outcome_score": outcome_score}),
                             return_exceptions=True)
