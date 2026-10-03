"""Offline evaluation + system-health report.

Runs the held-out eval cases (never indexed) through the real pipeline and reports:
* triage - intent/product/severity accuracy and macro-F1, P1 recall, sentiment agreement;
* retrieval - KB Recall@k / MRR@10 and same-intent ticket Hit@5, with an ablation of
  dense vs sparse vs hybrid vs hybrid+rerank;
* clarification - intent accuracy of the intake posterior before/after the adaptive questions, with an
  oracle customer who answers truthfully, plus questions asked and information gained;
* generation/safety - citation validity (must be 100%), citation coverage, LLM-judged step support on a
  sample, abstention precision/recall on unanswerable cases, unsafe self-service routes (must be 0);
* health - per-stage latency p50/p95, degraded-mode rate, LLM fallback rate.
Writes reports/eval_<timestamp>.{json,md} and stores the run in `eval_runs` (shown in the admin UI).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .ai.clarify import ClarifyEngine
from .ai.prompts import JUDGE_SYSTEM
from .config import ROOT
from .db import eval_runs, utc_now
from .gateways.llm import LLMUnavailable
from .pii import redact_text
from .seed import read_jsonl
from .tickets.desk import SupportDesk

SENTIMENT = {"frustrated": "negative", "angry": "negative", "concerned": "negative", "negative": "negative",
             "neutral": "neutral", "positive": "positive"}


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    labels = {g for g, _ in pairs} | {p for _, p in pairs}
    scores = []
    for label in labels:
        tp = sum(1 for g, p in pairs if g == label and p == label)
        fp = sum(1 for g, p in pairs if g != label and p == label)
        fn = sum(1 for g, p in pairs if g == label and p != label)
        if tp + fp + fn == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def oracle_answer(question: dict, expected_intent: str | None) -> tuple[list[str], str | None]:
    """A truthful customer: pick the option typical of their real issue, otherwise the neutral one."""
    if question.get("type") == "text":
        return [], "I'm not sure what else to add."
    options = question.get("options") or []
    if question.get("type") == "multi":
        return [options[-1]["id"]] if options else [], None
    for option in options:
        if expected_intent and expected_intent in (option.get("likely") or {}):
            return [option["id"]], None
    neutral = next((o for o in options if o.get("neutral")), None)
    if question["id"] == "closest" and expected_intent in (None, "other"):
        return ["other"], None
    return [neutral["id"] if neutral else options[-1]["id"]], None


async def judge_support(desk: SupportDesk, steps: list[dict], sources: list[dict]) -> list[bool] | None:
    if desk.s.llm is None or not steps:
        return None
    by_id = {s["id"]: s for s in sources}
    items = [{"n": i, "step": st["text"], "cited_text": " | ".join(
        (by_id.get(c) or {}).get("snippet") or "" for c in st.get("citations", []))} for i, st in enumerate(steps)]
    try:
        result = await desk.s.llm.json("judge", JUDGE_SYSTEM, json.dumps(items, ensure_ascii=False), None,
                                       max_tokens=800, name="eval.judge")
    except LLMUnavailable:
        return None
    verdicts = {int(v.get("n", -1)): bool(v.get("supported")) for v in result.data.get("verdicts", [])}
    return [verdicts.get(i, False) for i in range(len(steps))]


async def retrieval_ablation(desk: SupportDesk, cases: list[dict]) -> dict:
    modes = {"dense": dict(mode="dense", rerank=False), "sparse": dict(mode="sparse", rerank=False),
             "hybrid": dict(mode="hybrid", rerank=False), "hybrid_rerank": dict(mode="hybrid", rerank=True)}
    out = {}
    answerable = [c for c in cases if not c["should_abstain"]]
    for name, kwargs in modes.items():
        recall5, mrr, hit5 = [], [], []
        for case in answerable:
            result = await desk.s.retriever.search(redact_text(case["complaint"]), top_tickets=10, top_kb=10, **kwargs)
            kb_ids = list(dict.fromkeys(s["kb_id"] for s in result.kb))
            relevant = set(case["relevant_kb_ids"])
            recall5.append(1.0 if relevant & set(kb_ids[:5]) else 0.0)
            rank = next((i + 1 for i, k in enumerate(kb_ids[:10]) if k in relevant), None)
            mrr.append(1 / rank if rank else 0.0)
            hit5.append(1.0 if any(t.get("intent") == case["expected_intent"] for t in result.tickets[:5]) else 0.0)
        out[name] = {"kb_recall@5": round(statistics.mean(recall5), 4), "kb_mrr@10": round(statistics.mean(mrr), 4),
                     "ticket_intent_hit@5": round(statistics.mean(hit5), 4), "cases": len(answerable)}
    return out


async def run_eval(desk: SupportDesk, limit: int | None = None, concurrency: int = 2, judge_sample: int = 12,
                   ablation: bool = True, out_dir: Path | None = None) -> dict:
    cases = read_jsonl("eval_cases.jsonl")
    if limit:
        # stratified: keep every abstain case plus the first N answerable ones
        cases = [c for c in cases if c["should_abstain"]] + [c for c in cases if not c["should_abstain"]][:limit]
    semaphore = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(case: dict) -> None:
        async with semaphore:
            started = time.perf_counter()
            redacted = redact_text(case["complaint"])
            # 1) adaptive intake with an oracle customer
            state = await desk.s.clarify.start(case["complaint"], None, None)
            before = ClarifyEngine.leading_intent(state)[0]
            asked = 0
            while state["next_question"] and asked < 6:
                options, text = oracle_answer(state["next_question"], case["expected_intent"])
                state = await desk.s.clarify.answer(state, state["next_question"], options, text)
                asked += 1
            after = ClarifyEngine.leading_intent(state)[0]
            gain = sum(a.get("information_gain_bits") or 0 for a in state["answers"])
            # 2) full pipeline on the raw complaint (no intake) - the hardest setting for triage
            trace_id = f"eval_{uuid.uuid4().hex[:10]}"
            result = await desk.run_analysis(redacted, None, case.get("product_hint"), trace_id)
            rows.append({"case": case, "result": result, "intake_before": before, "intake_after": after,
                         "questions": asked, "info_gain": gain, "wall_ms": round((time.perf_counter() - started) * 1000)})

    await asyncio.gather(*(one(c) for c in cases))
    answerable = [r for r in rows if not r["case"]["should_abstain"]]
    unanswerable = [r for r in rows if r["case"]["should_abstain"]]

    def triage(r):
        return r["result"]["triage"]

    intent_pairs = [(r["case"]["expected_intent"], triage(r)["intent"]) for r in answerable]
    product_pairs = [(r["case"]["expected_product"], triage(r)["product"]) for r in answerable]
    severity_pairs = [(r["case"]["expected_severity"], triage(r)["severity"]) for r in answerable]
    sentiment_pairs = [(SENTIMENT.get(r["case"].get("expected_sentiment") or "", "neutral"), triage(r).get("sentiment"))
                       for r in answerable if r["case"].get("expected_sentiment")]
    p1 = [r for r in answerable if r["case"]["expected_severity"] == "P1"]
    routes = Counter(r["result"]["decision"]["route"] for r in rows)
    unsafe = [r["case"]["case_id"] for r in rows if r["result"]["decision"]["route"] == "self_service" and (
        r["case"]["expected_severity"] == "P1" or r["case"]["should_abstain"])]
    abstain_pred = {r["case"]["case_id"] for r in rows if r["result"]["decision"]["route"] == "human"
                    and (triage(r)["intent"] == "other" or (r["result"]["draft"] or {}).get("abstain")
                         or r["result"]["top_similarity"] < desk.s.settings.min_retrieval_score
                         or triage(r).get("prompt_injection"))}
    abstain_true = {r["case"]["case_id"] for r in unanswerable}
    tp = len(abstain_pred & abstain_true)
    drafts = [r["result"]["draft"] for r in rows if r["result"]["draft"]]
    validity = [d.get("citation_validity", 1.0) for d in drafts]
    coverage = [d.get("citation_coverage", 0.0) for d in drafts if not d.get("abstain")]
    judged: list[bool] = []
    for r in [r for r in answerable if (r["result"]["draft"] or {}).get("customer_steps")][:judge_sample]:
        verdicts = await judge_support(desk, r["result"]["draft"]["customer_steps"], r["result"]["sources"])
        if verdicts:
            judged += verdicts
    stage = defaultdict(list)
    for r in rows:
        for key in ("retrieval", "triage", "draft", "total"):
            if key in r["result"]["latency_ms"]:
                stage[key].append(r["result"]["latency_ms"][key])
    fallback = sum(1 for r in rows for p in (r["result"]["models"]["fallback_path"] or {}).values() if p == "fallback")
    with_intake = [r for r in answerable]
    clean = [r for r in answerable if "triage_llm_unavailable" not in r["result"]["degraded"]]
    clean_p1 = [r for r in clean if r["case"]["expected_severity"] == "P1"]
    report = {
        "run_id": f"eval_{datetime.now(UTC):%Y%m%d_%H%M%S}", "cases": len(rows), "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "triage": {
            "intent_accuracy": round(sum(g == p for g, p in intent_pairs) / max(1, len(intent_pairs)), 4),
            "intent_macro_f1": macro_f1(intent_pairs),
            "product_accuracy": round(sum(g == p for g, p in product_pairs) / max(1, len(product_pairs)), 4),
            "severity_accuracy": round(sum(g == p for g, p in severity_pairs) / max(1, len(severity_pairs)), 4),
            "severity_macro_f1": macro_f1(severity_pairs),
            "p1_recall": round(sum(1 for r in p1 if triage(r)["severity"] == "P1") / max(1, len(p1)), 4),
            "sentiment_agreement": round(sum(g == p for g, p in sentiment_pairs) / max(1, len(sentiment_pairs)), 4),
            "llm_available_cases": len(clean),
            "intent_macro_f1_llm_available": macro_f1([(r["case"]["expected_intent"], triage(r)["intent"]) for r in clean]),
            "severity_macro_f1_llm_available": macro_f1([(r["case"]["expected_severity"], triage(r)["severity"])
                                                         for r in clean]),
            "p1_recall_llm_available": round(sum(1 for r in clean_p1 if triage(r)["severity"] == "P1")
                                             / max(1, len(clean_p1)), 4),
            "by_language": {
                lang: round(sum(1 for r in answerable if r["case"]["language"] == lang
                                and triage(r)["intent"] == r["case"]["expected_intent"])
                            / max(1, sum(1 for r in answerable if r["case"]["language"] == lang)), 4)
                for lang in sorted({r["case"]["language"] for r in answerable})},
        },
        "clarification": {
            "intent_accuracy_before_questions": round(sum(r["intake_before"] == r["case"]["expected_intent"]
                                                          for r in with_intake) / max(1, len(with_intake)), 4),
            "intent_accuracy_after_questions": round(sum(r["intake_after"] == r["case"]["expected_intent"]
                                                         for r in with_intake) / max(1, len(with_intake)), 4),
            "mean_questions": round(statistics.mean([r["questions"] for r in rows]), 2) if rows else 0,
            "mean_information_gain_bits": round(statistics.mean([r["info_gain"] for r in rows]), 3) if rows else 0,
        },
        "routing": {"distribution": dict(routes), "unsafe_self_service": unsafe,
                    "abstention_precision": round(tp / max(1, len(abstain_pred)), 4),
                    "abstention_recall": round(tp / max(1, len(abstain_true)), 4)},
        "generation": {"citation_validity": round(min(validity), 4) if validity else 1.0,
                       "mean_citation_coverage": round(statistics.mean(coverage), 4) if coverage else None,
                       "judged_steps": len(judged),
                       "step_support_rate": round(sum(judged) / len(judged), 4) if judged else None},
        "health": {
            "latency_ms": {k: {"p50": pct(v, 0.5), "p95": pct(v, 0.95)} for k, v in stage.items()},
            "degraded_rate": round(sum(1 for r in rows if r["result"]["degraded"]) / max(1, len(rows)), 4),
            "degraded_reasons": dict(Counter(d for r in rows for d in r["result"]["degraded"])),
            "llm_fallback_calls": fallback,
        },
        "retrieval": {},
        "config": desk.s.settings.redacted(),
        "errors": [{"case": r["case"]["case_id"], "expected": r["case"]["expected_intent"],
                    "predicted": triage(r)["intent"], "route": r["result"]["decision"]["route"]}
                   for r in answerable if triage(r)["intent"] != r["case"]["expected_intent"]][:15],
    }
    if ablation:
        report["retrieval"] = await retrieval_ablation(desk, [r["case"] for r in rows])
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=ROOT, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        sha = None
    with desk.s.db.tx() as con:
        con.execute(eval_runs.insert().values(run_id=report["run_id"], git_sha=sha, config=report["config"],
                                              metrics={k: v for k, v in report.items() if k != "config"},
                                              created_at=utc_now()))
    out_dir = out_dir or ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{report['run_id']}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                      encoding="utf-8")
    (out_dir / f"{report['run_id']}.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def to_markdown(r: dict) -> str:
    t, c, g, rt, h = r["triage"], r["clarification"], r["generation"], r["routing"], r["health"]
    lines = [f"# Evaluation report `{r['run_id']}`", "",
             f"{r['cases']} held-out cases ({r['answerable']} answerable, {r['unanswerable']} should-abstain). "
             "Synthetic data: treat as a development baseline, not production accuracy.", "",
             "## Triage", "", "| Metric | Value | Target |", "|---|---|---|",
             f"| Intent accuracy | {t['intent_accuracy']:.1%} | - |",
             f"| Intent macro-F1 | {t['intent_macro_f1']:.3f} | >= 0.80 |",
             f"| Product accuracy | {t['product_accuracy']:.1%} | >= 90% |",
             f"| Severity macro-F1 | {t['severity_macro_f1']:.3f} | >= 0.70 |",
             f"| P1 recall | {t['p1_recall']:.1%} | >= 95% |",
             f"| Sentiment agreement | {t['sentiment_agreement']:.1%} | - |", "",
             f"On the {t['llm_available_cases']} answerable cases where the triage LLM was reachable: intent macro-F1 "
             f"{t['intent_macro_f1_llm_available']:.3f}, severity macro-F1 {t['severity_macro_f1_llm_available']:.3f}, "
             f"P1 recall {t['p1_recall_llm_available']:.1%}.", "",
             "Intent accuracy by language: " + ", ".join(f"{k} {v:.0%}" for k, v in t["by_language"].items()), "",
             "## Adaptive clarification (oracle customer)", "", "| Metric | Value |", "|---|---|",
             f"| Intent accuracy from complaint only (k-NN prior) | {c['intent_accuracy_before_questions']:.1%} |",
             f"| Intent accuracy after questions | {c['intent_accuracy_after_questions']:.1%} |",
             f"| Mean questions asked | {c['mean_questions']} |",
             f"| Mean information gained | {c['mean_information_gain_bits']} bits |", "",
             "## Routing and safety", "", f"Distribution: {rt['distribution']}", "",
             f"- Unsafe self-service routes (P1 or unanswerable): **{len(rt['unsafe_self_service'])}** (target 0)",
             f"- Abstention precision / recall: {rt['abstention_precision']:.1%} / {rt['abstention_recall']:.1%}",
             f"- Citation validity: {g['citation_validity']:.0%} (enforced in code)",
             f"- Mean citation coverage: {g['mean_citation_coverage']}",
             f"- LLM-judged step support: {g['step_support_rate']} over {g['judged_steps']} steps", ""]
    if r.get("retrieval"):
        lines += ["## Retrieval ablation", "", "| Mode | KB Recall@5 | KB MRR@10 | Ticket intent Hit@5 |",
                  "|---|---|---|---|"]
        lines += [f"| {m} | {v['kb_recall@5']:.3f} | {v['kb_mrr@10']:.3f} | {v['ticket_intent_hit@5']:.3f} |"
                  for m, v in r["retrieval"].items()]
        lines.append("")
    lines += ["## System health", "", "| Stage | p50 ms | p95 ms |", "|---|---|---|"]
    lines += [f"| {k} | {v['p50']} | {v['p95']} |" for k, v in h["latency_ms"].items()]
    lines += ["", f"Degraded-mode rate: {h['degraded_rate']:.1%} {h['degraded_reasons']}; "
              f"LLM fallback calls: {h['llm_fallback_calls']}", ""]
    if r["errors"]:
        lines += ["## Triage errors (first 15)", "", "| Case | Expected | Predicted | Route |", "|---|---|---|---|"]
        lines += [f"| {e['case']} | {e['expected']} | {e['predicted']} | {e['route']} |" for e in r["errors"]]
    return "\n".join(lines) + "\n"
