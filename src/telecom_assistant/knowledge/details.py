"""Full, admin-only records behind retrieval citation IDs."""

from __future__ import annotations

import sqlalchemy as sa

from ..db import Database, corpus_tickets, kb_articles, tickets
from .indexer import kb_chunks


def source_detail(db: Database, source_id: str) -> dict | None:
    """Resolve an indexed ticket ID or an exact KB section ID from the database."""
    if not source_id or len(source_id) > 120:
        return None
    with db.read() as con:
        if "#" in source_id:
            kb_id = source_id.split("#", 1)[0]
            row = con.execute(sa.select(kb_articles).where(kb_articles.c.kb_id == kb_id)).first()
            if not row:
                return None
            article = dict(row._mapping)
            section = next((chunk for chunk in kb_chunks(article) if chunk["source_id"] == source_id), None)
            if not section:
                return None
            return {"id": source_id, "kind": "kb", "section": section["section"],
                    "section_text": section["text"], "article": article}

        row = con.execute(sa.select(corpus_tickets).where(corpus_tickets.c.ticket_id == source_id)).first()
        if not row:
            return None
        case = dict(row.payload)
        related_ticket_id = source_id.removeprefix("LRN-") if row.source == "learned" else None
        if related_ticket_id and not con.execute(sa.select(tickets.c.id).where(
                tickets.c.id == related_ticket_id)).first():
            related_ticket_id = None
        return {"id": source_id, "kind": "ticket", "case": case, "status": row.status,
                "version": row.version, "origin": row.source, "outcome_score": row.outcome_score,
                "related_ticket_id": related_ticket_id}
