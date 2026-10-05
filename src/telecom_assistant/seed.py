"""Load the synthetic corpus (taxonomy, KB with customer self-help sections, resolved/unresolved tickets) and
replay versioned update events through the same indexer used for live data."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .config import ROOT
from .knowledge.indexer import StaleVersion
from .services import Services

DATA = ROOT / "data" / "synthetic" / "v1"


def read_jsonl(name: str, base: Path = DATA) -> list[dict]:
    return [json.loads(line) for line in (base / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def self_help() -> dict[str, list[str]]:
    raw = json.loads(resources.files("telecom_assistant.resources").joinpath("kb_self_help.json")
                     .read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def kb_records() -> list[dict]:
    helps = self_help()
    return [{"kb_id": a["kb_id"], "version": a["article_version"], "title": a["title"], "product": a["product"],
             "intent": a["intent"], "summary": a["summary"], "checks": a["checks"],
             "self_help": helps.get(a["kb_id"], []), "escalation": a["escalation_criteria"],
             "status": a["status"], "origin": "seed"} for a in read_jsonl("kb_articles.jsonl")]


async def bootstrap(services: Services) -> dict:
    added = services.registry.seed()
    await services.indexer.ensure()
    kb = {"inserted": 0, "updated": 0, "unchanged": 0, "status_changed": 0, "skipped_newer": 0}
    for article in kb_records():
        try:
            kb[await services.indexer.upsert_kb(article, actor="seed")] += 1
        except StaleVersion:
            # A newer published or deprecated KB version must not be reset by an older seed file.
            kb["skipped_newer"] += 1
    resolved = await services.indexer.index_tickets(read_jsonl("resolved_tickets.jsonl"))
    unresolved = await services.indexer.index_tickets(read_jsonl("unresolved_tickets.jsonl"))
    return {"taxonomy_added": added, "kb": kb, "resolved": resolved.as_dict(), "unresolved": unresolved.as_dict(),
            "index_counts": {"tickets": await services.index.count("tickets"), "kb": await services.index.count("kb")}}


async def apply_updates(services: Services) -> list[dict]:
    """Replay update_events.jsonl: unresolved -> resolved transitions, a KB edit and a KB deprecation."""
    import sqlalchemy as sa

    from .db import corpus_tickets, kb_articles

    results = []
    for event in read_jsonl("update_events.jsonl"):
        if event["entity_type"] == "ticket":
            with services.db.read() as con:
                row = con.execute(sa.select(corpus_tickets.c.payload).where(
                    corpus_tickets.c.ticket_id == event["entity_id"])).first()
            if not row:
                results.append({"event": event["event_id"], "result": "missing"})
                continue
            updated = {**row.payload, "record_version": event["new_version"], "status": event["new_status"],
                       "resolved_at": event["effective_at"], "resolution_steps": event["resolution_steps"],
                       "resolution_summary": event["resolution_summary"],
                       "closure_evidence": event["closure_evidence"]}
            report = await services.indexer.index_tickets([updated])
            results.append({"event": event["event_id"], "result": report.as_dict()})
        else:
            with services.db.read() as con:
                row = con.execute(sa.select(kb_articles).where(kb_articles.c.kb_id == event["entity_id"])).first()
            if not row:
                results.append({"event": event["event_id"], "result": "missing"})
                continue
            article = {**dict(row._mapping), "version": event["new_version"], "status": event["new_status"]}
            if "checks" in event:
                article["checks"] = event["checks"]
            results.append({"event": event["event_id"], "result": await services.indexer.upsert_kb(article)})
    return results
