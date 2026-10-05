"""Validate the synthetic telecom dataset and print a compact quality report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "synthetic" / "v1"


def read(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines()]


def validate() -> dict:
    resolved = read("resolved_tickets.jsonl")
    unresolved = read("unresolved_tickets.jsonl")
    articles = read("kb_articles.jsonl")
    evals = read("eval_cases.jsonl")
    events = read("update_events.jsonl")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    resources_dir = ROOT / "src" / "telecom_assistant" / "resources"
    taxonomy = json.loads((resources_dir / "taxonomy_seed.json").read_text(encoding="utf-8"))
    self_help = json.loads((resources_dir / "kb_self_help.json").read_text(encoding="utf-8"))
    precautions = json.loads((resources_dir / "precaution_sections.json").read_text(encoding="utf-8"))
    assert len(resolved) == manifest["counts"]["resolved_tickets"]
    assert len(unresolved) == manifest["counts"]["unresolved_tickets"]
    assert len(articles) == manifest["counts"]["kb_articles"]
    assert len(evals) == manifest["counts"]["eval_cases"]
    assert len(events) == manifest["counts"]["update_events"]
    assert len({r["ticket_id"] for r in resolved + unresolved}) == len(resolved + unresolved)
    assert len({r["kb_id"] for r in articles}) == len(articles)
    assert len({r["case_id"] for r in evals}) == len(evals)
    assert len({r["body"] for r in resolved + unresolved}) == len(resolved + unresolved), "Duplicate indexed complaint"
    assert len({r["complaint"] for r in evals}) == len(evals), "Duplicate eval complaint"
    assert not ({r["body"] for r in resolved + unresolved} & {r["complaint"] for r in evals}), "Exact train/eval leakage"
    kb_ids = {r["kb_id"] for r in articles}
    intents = {r["intent"] for r in resolved + unresolved}
    assert intents == {r["intent"] for r in taxonomy["classes"]}
    assert kb_ids == {key for key in self_help if not key.startswith("_")}
    assert all(self_help[kb_id] for kb_id in kb_ids)
    assert all(kb_id in self_help and all(1 <= number <= len(self_help[kb_id]) for number in numbers)
               for kb_id, numbers in precautions.items() if not kb_id.startswith("_"))
    assert all(r["status"] == "resolved" and r["resolution_steps"] and r["closure_evidence"] for r in resolved)
    assert all(r["status"] == "unresolved" and not r["resolution_steps"] and r["resolution_summary"] is None and r["closure_evidence"] is None for r in unresolved)
    assert all(set(r["kb_reference_ids"]) <= kb_ids for r in resolved)
    assert all(not r["kb_reference_ids"] for r in unresolved)
    assert all(set(r["relevant_kb_ids"]) <= kb_ids for r in evals)
    assert all(not r["relevant_kb_ids"] and r["should_abstain"] for r in evals if r["expected_intent"] == "other")
    assert all(r["source"] == "synthetic_telecom_v1" for r in resolved + unresolved + articles + evals)
    assert {r["language"] for r in resolved + unresolved + evals} == {"en"}, "dataset must be English-only"
    assert all(r["record_version"] == 1 for r in resolved + unresolved)
    assert len({r["event_id"] for r in events}) == len(events)
    unresolved_ids = {r["ticket_id"] for r in unresolved}
    assert all(e["entity_id"] in unresolved_ids for e in events if e["entity_type"] == "ticket")
    assert all(e["entity_id"] in kb_ids for e in events if e["entity_type"] == "kb")
    assert all(e["new_version"] == 2 for e in events)
    return {
        "counts": {"resolved": len(resolved), "unresolved": len(unresolved), "kb": len(articles), "eval": len(evals), "updates": len(events)},
        "intents": len(intents),
        "languages_train": dict(Counter(r["language"] for r in resolved + unresolved)),
        "languages_eval": dict(Counter(r["language"] for r in evals)),
        "edge_tags": dict(Counter(tag for r in resolved + unresolved + evals for tag in r["edge_case_tags"])),
        "abstain_eval_count": sum(r["should_abstain"] for r in evals),
        "missing_subject_train": sum(r["subject"] is None for r in resolved + unresolved),
        "missing_product_hint_train": sum(r["product_hint"] is None for r in resolved + unresolved),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
