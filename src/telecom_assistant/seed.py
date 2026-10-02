"""Load the synthetic corpus and replay versioned update events locally."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import Settings
from .knowledge import KnowledgeStore, document_text
from .ollama import OllamaClient

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic" / "v1"


def read_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines()]


async def ingest_batch(store: KnowledgeStore, model: OllamaClient, kind: str, records: list[dict]) -> dict:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    for start in range(0, len(records), 16):
        batch = records[start : start + 16]
        vectors = await model.embed_many([document_text(kind, row) for row in batch])
        for row, vector in zip(batch, vectors):
            counts[store.upsert(kind, row, vector)] += 1
    return counts


async def bootstrap(settings: Settings) -> None:
    store = KnowledgeStore(settings.knowledge_db, settings.embed_model)
    model = OllamaClient(settings.ollama_url, settings.embed_model, settings.chat_model)
    resolved = read_jsonl("resolved_tickets.jsonl")
    unresolved = read_jsonl("unresolved_tickets.jsonl")
    articles = read_jsonl("kb_articles.jsonl")
    for intent, info in json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))["intents"].items():
        example = next(row for row in resolved if row["intent"] == intent)
        from .knowledge import TaxonomyInput

        if not any(item["intent"] == intent for item in store.taxonomy()):
            store.upsert_taxonomy(TaxonomyInput(
                intent=intent, category=example["category"], product=info["product"],
                description=intent.replace(".", " ").replace("_", " "),
            ))
    for name, kind, rows in [
        ("resolved", "ticket", resolved), ("unresolved", "ticket", unresolved), ("kb", "kb", articles)
    ]:
        print(name, await ingest_batch(store, model, kind, rows))
    print("counts", store.counts())


async def apply_updates(settings: Settings) -> None:
    store = KnowledgeStore(settings.knowledge_db, settings.embed_model)
    model = OllamaClient(settings.ollama_url, settings.embed_model, settings.chat_model)
    events = read_jsonl("update_events.jsonl")
    for event in events:
        current = store.get_record(event["entity_id"])
        if current is None:
            raise ValueError(f"Cannot update missing record {event['entity_id']}; run bootstrap first")
        updated = dict(current)
        if event["entity_type"] == "ticket":
            updated["record_version"] = event["new_version"]
            updated["status"] = event["new_status"]
            updated["resolved_at"] = event["effective_at"]
            updated["resolution_steps"] = event["resolution_steps"]
            updated["resolution_summary"] = event["resolution_summary"]
            updated["closure_evidence"] = event["closure_evidence"]
            updated["resolution_outcome"] = "verified_fixed_synthetic"
            kind = "ticket"
        else:
            updated["article_version"] = event["new_version"]
            updated["status"] = event["new_status"]
            updated["updated_at"] = event["effective_at"]
            if "checks" in event:
                updated["checks"] = event["checks"]
            kind = "kb"
        vector = (await model.embed_many([document_text(kind, updated)]))[0]
        print(event["event_id"], store.upsert(kind, updated, vector))
    print("counts", store.counts())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["bootstrap", "updates"])
    args = parser.parse_args()
    settings = Settings.from_env()
    asyncio.run(bootstrap(settings) if args.action == "bootstrap" else apply_updates(settings))


if __name__ == "__main__":
    main()
