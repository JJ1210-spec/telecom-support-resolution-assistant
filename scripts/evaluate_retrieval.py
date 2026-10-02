"""Evaluate KB retrieval on the held-out synthetic complaints."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from telecom_assistant.config import Settings
from telecom_assistant.knowledge import KnowledgeStore
from telecom_assistant.ollama import OllamaClient

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "synthetic" / "v1" / "eval_cases.jsonl"
REPORT = ROOT / "reports" / "retrieval_eval.json"


async def main() -> None:
    settings = Settings.from_env()
    store = KnowledgeStore(settings.knowledge_db, settings.embed_model)
    model = OllamaClient(settings.ollama_url, settings.embed_model, settings.chat_model)
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()]
    started = time.perf_counter()
    results = []
    top_scores = []
    for offset in range(0, len(cases), 16):
        batch = cases[offset : offset + 16]
        vectors = await model.embed_many([case["complaint"] for case in batch])
        for case, vector in zip(batch, vectors):
            nearest = store.search(vector, "evidence", None, 1)
            top_scores.append({"case_id": case["case_id"], "should_abstain": case["should_abstain"],
                               "top_score": nearest[0]["score"] if nearest else -1.0})
            if not case["relevant_kb_ids"]:
                continue
            top = store.search(vector, "evidence", "kb", 3)
            ids = [row["source_id"] for row in top]
            expected = case["relevant_kb_ids"]
            results.append({
                "case_id": case["case_id"], "expected": expected,
                "top3": ids, "hit_at_1": bool(ids and ids[0] in expected),
                "hit_at_3": bool(set(ids) & set(expected)),
            })
    report = {
        "as_of_utc": datetime.now(UTC).isoformat(),
        "dataset": "synthetic_telecom_v1 unindexed_development_eval",
        "embedding_model": settings.embed_model,
        "answerable_cases": len(results),
        "recall_at_1": round(sum(row["hit_at_1"] for row in results) / len(results), 4),
        "recall_at_3": round(sum(row["hit_at_3"] for row in results) / len(results), 4),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "score_analysis": {
            "answerable_median": round(statistics.median(r["top_score"] for r in top_scores if not r["should_abstain"]), 4),
            "abstain_median": round(statistics.median(r["top_score"] for r in top_scores if r["should_abstain"]), 4),
            "thresholds": [
                {"threshold": threshold,
                 "answerable_kept": sum(r["top_score"] >= threshold for r in top_scores if not r["should_abstain"]),
                 "abstain_rejected": sum(r["top_score"] < threshold for r in top_scores if r["should_abstain"])}
                for threshold in (0.55, 0.60, 0.65, 0.70)
            ],
            "abstain_cases": [r for r in top_scores if r["should_abstain"]],
        },
        "misses_at_3": [row for row in results if not row["hit_at_3"]],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("misses_at_3", "score_analysis")}, indent=2))
    print(json.dumps(report["score_analysis"], indent=2))
    print("misses_at_3", len(report["misses_at_3"]))


if __name__ == "__main__":
    asyncio.run(main())
