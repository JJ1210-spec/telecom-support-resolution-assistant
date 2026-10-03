"""Data-drift monitoring with actions, not just charts.

Four kinds of drift are tracked over a recent window against a reference:

1. **Label / prior drift** - PSI of the intent, product and severity distributions of live tickets vs the
   indexed corpus (PSI > 0.2 = significant shift). Action: rebalance few-shots / review routing thresholds.
2. **Coverage (covariate) drift** - out-of-distribution rate (closest past case below `OOD_SIMILARITY`),
   mean top-1 similarity, and the complaint-embedding centroid shift between consecutive windows.
   Action: run new-class discovery; write KB for the uncovered cluster.
3. **Taxonomy drift** - rate of `other`, low-confidence and k-NN/LLM disagreement. Action: discovery job.
4. **Solution (concept) drift** - per KB article, the customer-reported success rate of its steps
   (worked vs did-not-work) in the recent window vs all time. A fix that used to work and now fails
   (e.g. after a firmware or policy change) flags the article for review automatically.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
import sqlalchemy as sa

from ..config import Settings
from ..db import Database, bytes_to_vec, corpus_tickets, drift_snapshots, kb_articles, steps, tickets, utc_now

EPS = 1e-4
MIN_SAMPLE = 30


def psi(expected: dict[str, float], actual: dict[str, float]) -> float:
    keys = set(expected) | set(actual)
    total = 0.0
    for key in keys:
        e = max(expected.get(key, 0.0), EPS)
        a = max(actual.get(key, 0.0), EPS)
        total += (a - e) * math.log(a / e)
    return round(total, 4)


def distribution(values: list[str | None]) -> dict[str, float]:
    counts = Counter(v or "unknown" for v in values)
    total = sum(counts.values())
    return {k: round(v / total, 4) for k, v in counts.items()} if total else {}


class DriftMonitor:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings, self.db = settings, db

    def _windows(self, days: int):
        now = utc_now()
        recent_start, previous_start = now - timedelta(days=days), now - timedelta(days=2 * days)
        cols = (tickets.c.intent, tickets.c.product, tickets.c.severity, tickets.c.confidence, tickets.c.top_similarity,
                tickets.c.triage, tickets.c.embedding, tickets.c.created_at)
        with self.db.read() as con:
            recent = con.execute(sa.select(*cols).where(tickets.c.created_at >= recent_start,
                                                        tickets.c.analysis_state == "done")).all()
            previous = con.execute(sa.select(*cols).where(tickets.c.created_at >= previous_start,
                                                          tickets.c.created_at < recent_start,
                                                          tickets.c.analysis_state == "done")).all()
            corpus = con.execute(sa.select(corpus_tickets.c.payload).where(corpus_tickets.c.status == "resolved")).all()
        return recent, previous, [r.payload for r in corpus]

    def solution_drift(self, days: int) -> list[dict]:
        since = utc_now() - timedelta(days=days)
        with self.db.read() as con:
            rows = con.execute(sa.select(steps.c.citations, steps.c.status, steps.c.status_at).where(
                steps.c.status.in_(["worked", "did_not_work"]))).all()
            titles = dict(con.execute(sa.select(kb_articles.c.kb_id, kb_articles.c.title)).all())
        stats: dict[str, dict] = defaultdict(lambda: {"all": [0, 0], "recent": [0, 0]})
        for row in rows:
            kb_ids = {c.split("#")[0] for c in (row.citations or []) if c.startswith("KB-")}
            worked = 1 if row.status == "worked" else 0
            status_at = row.status_at
            if status_at is not None and status_at.tzinfo is None:
                status_at = status_at.replace(tzinfo=since.tzinfo)
            for kb_id in kb_ids:
                stats[kb_id]["all"][0] += worked
                stats[kb_id]["all"][1] += 1
                if status_at and status_at >= since:
                    stats[kb_id]["recent"][0] += worked
                    stats[kb_id]["recent"][1] += 1
        out = []
        for kb_id, s in stats.items():
            all_rate = s["all"][0] / s["all"][1] if s["all"][1] else None
            recent_rate = s["recent"][0] / s["recent"][1] if s["recent"][1] else None
            flagged = bool(recent_rate is not None and s["recent"][1] >= 3 and (
                recent_rate < 0.4 or (all_rate is not None and s["all"][1] > s["recent"][1]
                                      and recent_rate - all_rate < -0.25)))
            out.append({"kb_id": kb_id, "title": titles.get(kb_id, kb_id), "attempts": s["all"][1],
                        "success_rate": round(all_rate, 3) if all_rate is not None else None,
                        "recent_attempts": s["recent"][1],
                        "recent_success_rate": round(recent_rate, 3) if recent_rate is not None else None,
                        "flagged": flagged})
        return sorted(out, key=lambda r: (not r["flagged"], -(r["attempts"])))

    def compute(self, days: int = 7, persist: bool = True) -> dict:
        recent, previous, corpus = self._windows(days)
        reference_intent = distribution([p.get("intent") for p in corpus])
        reference_product = distribution([p.get("product") for p in corpus])
        reference_severity = distribution([p.get("severity") for p in corpus])
        n = len(recent)
        intents = distribution([r.intent for r in recent])
        sims = [r.top_similarity for r in recent if r.top_similarity is not None]
        prev_sims = [r.top_similarity for r in previous if r.top_similarity is not None]
        triages = [r.triage or {} for r in recent]
        metrics = {
            "window_days": days, "tickets_in_window": n, "tickets_in_previous_window": len(previous),
            "psi_intent": psi(reference_intent, intents) if n else 0.0,
            "psi_product": psi(reference_product, distribution([r.product for r in recent])) if n else 0.0,
            "psi_severity": psi(reference_severity, distribution([r.severity for r in recent])) if n else 0.0,
            "psi_intent_vs_previous": psi(distribution([r.intent for r in previous]), intents)
            if n and previous else None,
            "other_rate": round(sum(1 for r in recent if r.intent in (None, "other")) / n, 3) if n else 0.0,
            "low_confidence_rate": round(sum(1 for r in recent if (r.confidence or 0) < 0.5) / n, 3) if n else 0.0,
            "ood_rate": round(sum(1 for s in sims if s < self.settings.ood_similarity) / len(sims), 3) if sims else 0.0,
            "mean_top_similarity": round(float(np.mean(sims)), 3) if sims else None,
            "previous_mean_top_similarity": round(float(np.mean(prev_sims)), 3) if prev_sims else None,
            "knn_disagreement_rate": round(sum(1 for t in triages if (t.get("knn_agreement") or 0) < 0.3) / n, 3)
            if n else 0.0,
            "centroid_shift": self._centroid_shift(recent, previous),
            "intent_distribution": intents, "reference_intent_distribution": reference_intent,
        }
        solution = self.solution_drift(days)
        alerts = self._alerts(metrics, solution)
        result = {"metrics": metrics, "solution_drift": solution, "alerts": alerts, "computed_at": utc_now().isoformat()}
        if persist:
            with self.db.tx() as con:
                con.execute(drift_snapshots.insert().values(metrics={**metrics, "alerts": len(alerts)},
                                                            created_at=utc_now()))
        return result

    @staticmethod
    def _centroid_shift(recent, previous) -> float | None:
        a = [bytes_to_vec(r.embedding) for r in recent if r.embedding]
        b = [bytes_to_vec(r.embedding) for r in previous if r.embedding]
        if len(a) < 3 or len(b) < 3:
            return None
        ca, cb = np.mean(a, axis=0), np.mean(b, axis=0)
        cos = float(np.dot(ca, cb) / ((np.linalg.norm(ca) * np.linalg.norm(cb)) or 1.0))
        return round(1 - cos, 4)

    def _alerts(self, m: dict, solution: list[dict]) -> list[dict]:
        alerts = []

        def add(level: str, metric: str, value, threshold, message: str, action: str, action_id: str) -> None:
            alerts.append({"level": level, "metric": metric, "value": value, "threshold": threshold,
                           "message": message, "action": action, "action_id": action_id})

        enough = m["tickets_in_window"] >= MIN_SAMPLE  # PSI and rates are noise on tiny samples
        if not enough:
            add("info", "tickets_in_window", m["tickets_in_window"], MIN_SAMPLE,
                "Few tickets in the window - distribution drift alerts are suppressed until the sample is large "
                "enough. Solution-drift alerts still apply.", "Wait for more traffic", "none")
        if enough and m["psi_intent"] > 0.2:
            add("high" if m["psi_intent"] > 0.35 else "medium", "psi_intent", m["psi_intent"], 0.2,
                "The mix of issues customers report has shifted from the historical corpus.",
                "Check incident radar for an emerging problem; review routing thresholds", "open_incidents")
        if enough and m["other_rate"] > 0.10:
            add("high", "other_rate", m["other_rate"], 0.10,
                "Over 10% of tickets fit no known issue class.", "Run new-class discovery", "run_discovery")
        if enough and m["ood_rate"] > 0.15:
            add("medium", "ood_rate", m["ood_rate"], 0.15,
                "Many complaints have no similar past case (coverage gap).",
                "Run discovery and write KB for uncovered clusters", "run_discovery")
        prev, cur = m.get("previous_mean_top_similarity"), m.get("mean_top_similarity")
        if prev and cur and cur < prev * 0.85:
            add("medium", "mean_top_similarity", cur, round(prev * 0.85, 3),
                "Top-1 retrieval similarity dropped >15% vs the previous window.",
                "Review KB gaps; consider reindexing with an updated corpus", "review_kb")
        if enough and m["low_confidence_rate"] > 0.25:
            add("medium", "low_confidence_rate", m["low_confidence_rate"], 0.25,
                "Triage confidence is low for over a quarter of tickets.", "Run discovery; inspect eval report",
                "run_discovery")
        if enough and (m.get("centroid_shift") or 0) > 0.05:
            add("medium", "centroid_shift", m["centroid_shift"], 0.05,
                "Complaint language has moved away from last window's.", "Inspect recent tickets", "none")
        for row in solution:
            if row["flagged"]:
                add("high", "solution_success_rate", row["recent_success_rate"], 0.4,
                    f"Steps from '{row['title']}' recently fail for customers "
                    f"({row['recent_success_rate']:.0%} success over {row['recent_attempts']} tries).",
                    f"Review KB article {row['kb_id']} - the fix may be outdated", f"review_kb:{row['kb_id']}")
        return alerts

    def history(self, limit: int = 30) -> list[dict]:
        with self.db.read() as con:
            rows = con.execute(sa.select(drift_snapshots).order_by(drift_snapshots.c.id.desc()).limit(limit)).all()
        return [{"created_at": r.created_at.isoformat(), **{k: v for k, v in r.metrics.items()
                                                             if not isinstance(v, dict)}} for r in reversed(rows)]

    def flag_kb(self, kb_id: str, reason: str) -> None:
        with self.db.tx() as con:
            con.execute(kb_articles.update().where(kb_articles.c.kb_id == kb_id).values(review_reason=reason))
