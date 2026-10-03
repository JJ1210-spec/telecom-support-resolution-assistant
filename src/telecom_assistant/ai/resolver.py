"""Grounded resolution drafting, citation validation, confidence gate and the routing decision.

Routing answers the product question "can the customer fix this themselves right now?":

* ``self_service`` — simple and recurring: low severity, confident triage, the same issue was solved
  >= N times before (recurrence), and at least one customer-safe step is grounded in a self-help KB
  section. The customer gets the steps immediately; the ticket stays live until they confirm.
* ``assisted`` — the customer still gets safe self-help steps, but the ticket also goes to the human
  queue (e.g. P2, churn risk, weaker evidence).
* ``human`` — complex, high-risk or unclear: P1, sensitive intents (billing disputes, identity, porting),
  unknown class, prompt-injection, abstention. No AI steps are shown to the customer; the agent gets a
  copilot brief instead.
Every decision carries the list of reasons that produced it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from ..config import Settings
from ..gateways.llm import LLMGateway, LLMUnavailable
from ..knowledge.retrieval import RetrievalResult, recurrence
from .prompts import DRAFT_SYSTEM, DRAFT_VERSION

UNSUPPORTED_COMMITMENT = re.compile(
    r"\b(refund|compensat\w*|waive\w*|free of charge|credit (of|to your)|guarantee\w*|within \d+\s*(hours?|days?|mins?)"
    r"|by (today|tonight|tomorrow)|technician will (arrive|visit))\b", re.IGNORECASE)
UNSAFE_CUSTOMER = re.compile(r"\b(factory reset|open the (router|ont) case|climb|share (your )?(otp|password|pin)"
                             r"|full card)\b", re.IGNORECASE)


class Cited(BaseModel):
    text: str = Field(min_length=3, max_length=600)
    citations: list[str] = Field(default_factory=list)


class CustomerStep(Cited):
    detail: str = ""


class AgentStep(Cited):
    pass


class DraftOut(BaseModel):
    probable_root_cause: Cited | None = None
    customer_steps: list[CustomerStep] = Field(default_factory=list)
    agent_steps: list[AgentStep] = Field(default_factory=list)
    customer_message: str = ""
    escalate_if: list[str] = Field(default_factory=list)
    abstain: bool = False
    abstain_reason: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


def validate_citations(draft: dict, sources: list[dict]) -> tuple[dict, list[str]]:
    """Code-enforced grounding: drop unknown citations, drop uncited steps, enforce the customer gate."""
    by_id = {s["id"]: s for s in sources}
    customer_ids = {s["id"] for s in sources if s.get("audience") == "customer"}
    warnings: list[str] = []
    out = dict(draft)

    demoted: list[dict] = []

    def clean(step: dict, customer: bool) -> dict | None:
        cited = list(dict.fromkeys(c for c in step.get("citations", []) if c in by_id))
        if len(cited) != len(step.get("citations", [])):
            warnings.append(f"Removed unknown citation(s) from step: {step['text'][:60]}")
        if not cited:
            warnings.append(f"Dropped uncited step: {step['text'][:60]}")
            return None
        if UNSUPPORTED_COMMITMENT.search(step["text"]):
            warnings.append(f"Dropped step with unsupported commitment: {step['text'][:60]}")
            return None
        if customer:
            if UNSAFE_CUSTOMER.search(step["text"]):
                warnings.append(f"Dropped unsafe customer action: {step['text'][:60]}")
                return None
            if not any(c in customer_ids for c in cited):
                warnings.append(f"Customer step not backed by a self-help section; moved to agent steps: "
                                f"{step['text'][:60]}")
                demoted.append({"text": step["text"], "citations": cited})
                return None
        return {**step, "citations": cited}

    raw_customer, raw_agent = draft.get("customer_steps", []), draft.get("agent_steps", [])
    out["customer_steps"] = [s for s in (clean(step, True) for step in raw_customer) if s]
    out["agent_steps"] = [s for s in (clean(step, False) for step in raw_agent) if s] + demoted
    root = draft.get("probable_root_cause")
    if root:
        cited = [c for c in root.get("citations", []) if c in by_id]
        out["probable_root_cause"] = {**root, "citations": cited} if cited else None
    if UNSUPPORTED_COMMITMENT.search(out.get("customer_message", "")):
        out["customer_message"] = ""
        warnings.append("Removed customer message containing an unsupported commitment")
    total = len(raw_customer) + len(raw_agent)
    kept = len(out["customer_steps"]) + len(out["agent_steps"])
    if total and kept / total < 0.5 and not out.get("abstain"):
        out["abstain"] = True
        out["abstain_reason"] = "More than half of the drafted steps failed citation validation"
    cited_steps = out["customer_steps"] + out["agent_steps"]
    out["citation_coverage"] = round(kept / total, 3) if total else 0.0
    out["citation_validity"] = 1.0 if all(c in by_id for s in cited_steps for c in s["citations"]) else 0.0
    return out, warnings


def decision_confidence(retrieval: RetrievalResult, triage: dict, draft: dict | None) -> float:
    sims = sorted((s.get("similarity") or 0 for s in retrieval.tickets + retrieval.kb), reverse=True)
    reranks = sorted((s.get("rerank") or 0 for s in retrieval.all_sources()), reverse=True)
    top = reranks[0] if reranks and reranks[0] else (sims[0] if sims else 0.0)
    mean3 = sum(sims[:3]) / max(1, len(sims[:3]))
    intents = [s.get("intent") for s in retrieval.tickets[:5]]
    agreement = intents.count(triage.get("intent")) / max(1, len(intents))
    coverage = (draft or {}).get("citation_coverage", 0.0)
    score = 0.25 * min(1.0, top) + 0.2 * mean3 + 0.25 * triage.get("confidence", 0) + 0.15 * coverage + 0.15 * agreement
    return round(score, 3)


def route(settings: Settings, triage: dict, retrieval: RetrievalResult, draft: dict | None,
          classes: dict[str, dict], intake: dict | None) -> dict:
    reasons: list[str] = []
    blockers: list[str] = []
    intent = triage.get("intent")
    severity = triage.get("severity", "P3")
    cls = classes.get(intent or "", {})
    repeat = recurrence(retrieval.tickets, intent, settings.strong_match_score)
    confidence = decision_confidence(retrieval, triage, draft)
    customer_steps = (draft or {}).get("customer_steps", [])
    if severity == "P1":
        blockers.append("P1 severity always goes to a human")
    if intent in (None, "other"):
        blockers.append("Issue does not match a known class (sent to discovery pool)")
    if cls.get("sensitive"):
        blockers.append(f"'{cls.get('label')}' needs a specialist (account, field or policy action)")
    if triage.get("prompt_injection"):
        blockers.append("Complaint contains instruction-like text")
    if draft is None:
        blockers.append("No draft could be generated (LLM unavailable)")
    elif draft.get("abstain"):
        blockers.append(f"Insufficient evidence: {draft.get('abstain_reason') or 'sources do not match'}")
    if retrieval.top_similarity < settings.min_retrieval_score:
        blockers.append(f"Closest past case is too dissimilar ({retrieval.top_similarity:.2f})")
    if (intake or {}).get("chose_other"):
        blockers.append("Customer said the issue is 'something else'")

    if blockers:
        decision = "human"
        reasons = blockers
    else:
        simple = severity in ("P3", "P4")
        confident = triage.get("confidence", 0) >= settings.self_service_min_confidence
        recurring = repeat >= settings.self_service_min_recurrence
        if simple and confident and recurring and customer_steps:
            decision = "self_service"
            reasons = [f"Recurring issue: {repeat} similar tickets were resolved before",
                       f"Severity {severity} with triage confidence {triage.get('confidence'):.2f}",
                       f"{len(customer_steps)} safe self-help step(s) grounded in the KB"]
        else:
            decision = "assisted" if customer_steps else "human"
            if not simple:
                reasons.append(f"Severity {severity} needs a human to stay involved")
            if not confident:
                reasons.append(f"Triage confidence {triage.get('confidence', 0):.2f} is below "
                               f"{settings.self_service_min_confidence}")
            if not recurring:
                reasons.append(f"Only {repeat} strongly similar past resolution(s)")
            if not customer_steps:
                reasons.append("No customer-safe step is grounded in a self-help KB section")
            if triage.get("churn_risk"):
                reasons.append("Churn risk detected")
    return {"route": decision, "reasons": reasons, "recurrence": repeat, "decision_confidence": confidence,
            "top_similarity": round(retrieval.top_similarity, 3)}


KBExpander = Callable[..., Awaitable[list[dict]]]  # (kb_ids, intent) -> KB sections


class Resolver:
    def __init__(self, settings: Settings, llm: LLMGateway | None, expand_kb: KBExpander) -> None:
        self.settings, self.llm, self.expand_kb = settings, llm, expand_kb

    async def evidence(self, retrieval: RetrievalResult, triage: dict) -> list[dict]:
        """Top KB articles expanded to all their sections + the strongest past tickets."""
        kb_ids = list(dict.fromkeys(s["kb_id"] for s in retrieval.kb if s.get("kb_id")))
        kb_sections = await self.expand_kb(kb_ids[:3], triage.get("intent"))
        scores = {s["kb_id"]: s.get("similarity") for s in retrieval.kb}
        for section in kb_sections:
            section["similarity"] = scores.get(section.get("kb_id"))
        tickets = [t for t in retrieval.tickets if (t.get("similarity") or 0) >= self.settings.min_retrieval_score][:3]
        # keep prompts small for free-tier token-per-minute limits: the intent's article in full, others only self-help
        primary = kb_sections[0]["kb_id"] if kb_sections else None
        kb_sections = [k for k in kb_sections if k["kb_id"] == primary or k.get("audience") == "customer"]
        return kb_sections + tickets

    async def draft(self, complaint: str, triage: dict, sources: list[dict], trace_id: str,
                    tried: list[str]) -> tuple[dict | None, dict]:
        meta: dict = {"prompt_version": DRAFT_VERSION, "degraded": [], "warnings": []}
        if not sources:
            return ({"abstain": True, "abstain_reason": "No similar resolved case or published article was found",
                     "customer_steps": [], "agent_steps": [], "citation_coverage": 0.0, "citation_validity": 1.0},
                    meta)
        if self.llm is None:
            meta["degraded"].append("draft_llm_disabled")
            return None, meta
        rendered = []
        for s in sources:
            body = s.get("text") or s.get("snippet") or ""
            if s["kind"] == "ticket":
                body = (f"Problem: {s.get('snippet', '')}\nRoot cause: {s.get('root_cause')}\n"
                        f"Steps: {'; '.join(s.get('steps') or [])}")
            rendered.append(f'<source id="{s["id"]}" type="{s["kind"]}" audience="{s.get("audience", "agent")}" '
                            f'similarity="{(s.get("similarity") or 0):.2f}">{body[:450]}</source>')
        user = (f"<complaint>\n{complaint[:3000]}\n</complaint>\n<triage>{json.dumps(self._triage_view(triage))}"
                f"</triage>\n<already_tried>{json.dumps(tried)}</already_tried>\n<sources>\n" + "\n".join(rendered)
                + "\n</sources>")
        system = DRAFT_SYSTEM
        try:
            result = await self.llm.json("draft", system, user, DraftOut, max_tokens=1000, trace_id=trace_id,
                                         name="draft")
        except LLMUnavailable as exc:
            meta["degraded"].append("draft_llm_unavailable")
            meta["attempts"] = exc.attempts
            return None, meta
        draft, warnings = validate_citations(result.data, sources)
        meta.update(model=result.model_id, fallback_path=result.fallback_path, attempts=result.attempts,
                    latency_ms=result.latency_ms, warnings=warnings)
        return draft, meta

    @staticmethod
    def _triage_view(triage: dict) -> dict:
        return {k: triage.get(k) for k in ("intent", "intent_label", "product", "severity", "severity_drivers",
                                           "sentiment", "entities", "language")}
