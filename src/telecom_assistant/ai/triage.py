"""Triage: intent, product, severity, sentiment and entities with confidence and evidence.

Three signals are combined and their agreement is reported:
1. the LLM, prompted with the *live* taxonomy (new classes work without retraining);
2. k-NN label votes from the most similar resolved tickets;
3. the clarifying-question posterior from intake (the customer's own structured answers).
Deterministic rules run after the model for P1 recall and churn risk, and are listed as drivers.
If every LLM provider is down, triage degrades to signals 2+3 instead of failing.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..gateways.llm import LLMGateway, LLMUnavailable
from .prompts import TRIAGE_SYSTEM, TRIAGE_VERSION

INJECTION = re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|prompts)|system prompt|you are now",
                       re.IGNORECASE)
CHURN = re.compile(r"\b(cancel|port[- ]?out|switch(ing)? (to|operator)|ombudsman|consumer court|trai|legal action|"
                   r"band kar|chhod (dunga|denge))\b", re.IGNORECASE)
# "kaam nahi kar raha" means "not working" in Hinglish, so bare "kaam" is deliberately not a work-impact cue.
WORK = re.compile(r"work from home|\bwfh\b|office|business|meeting|client|exam|घर से काम|office ka kaam", re.IGNORECASE)
EMERGENCY = re.compile(r"emergency|ambulance|hospital|elderly|112|911|safety|आपातकाल", re.IGNORECASE)
AREA = re.compile(r"whole (street|building|area|colony)|entire (building|area|street)|several (flats|homes|houses)|"
                  r"neighbou?rs|everyone in|पूरी (इमारत|गली)|पड़ोसी|poo?ri (building|gali|society)|padosi|"
                  r"sab ka|sabka", re.IGNORECASE)
TOTAL_LOSS = re.compile(r"no (internet|broa?d?band|brodband|service|signal)|internet is down|completely down|offline|"
                        r"not working at all|net band|net bilkul nahi|service (poori )?band|इंटरनेट बंद|सेवा बंद|"
                        r"ऑफलाइन|dead", re.IGNORECASE)
LOS = re.compile(r"\blos\b|एल\s?ओ\s?एस|लॉस", re.IGNORECASE)
RED = re.compile(r"red|लाल|\bla+l\b", re.IGNORECASE)
REPEAT = re.compile(r"again|every (day|evening|night)|more than once|repeated|third time|roz|बार बार|bar bar|"
                    r"already (restarted|tried|called)", re.IGNORECASE)
SEVERITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


class TriageEntities(BaseModel):
    time_pattern: str | None = None
    actions_tried: list[str] = Field(default_factory=list)
    device: str | None = None
    error_code: str | None = None
    impact: str | None = None


class TriageEvidence(BaseModel):
    intent: str = ""
    severity: str = ""
    sentiment: str = ""


class TriageOut(BaseModel):
    intent: str
    product: str = "Unknown"
    severity: Literal["P1", "P2", "P3", "P4"]
    severity_drivers: list[str] = Field(default_factory=list)
    sentiment: Literal["negative", "neutral", "positive"] = "neutral"
    sentiment_score: float = Field(default=0.0, ge=-1, le=1)
    emotions: list[str] = Field(default_factory=list)
    churn_risk: bool = False
    language: str = "en"
    confidence: float = Field(default=0.5, ge=0, le=1)
    entities: TriageEntities = Field(default_factory=TriageEntities)
    evidence: TriageEvidence = Field(default_factory=TriageEvidence)

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, value):
        value = str(value).upper().strip()
        return value if value in SEVERITY_ORDER else "P3"

    @field_validator("sentiment", mode="before")
    @classmethod
    def _sent(cls, value):
        value = str(value).lower().strip()
        return {"frustrated": "negative", "angry": "negative", "concerned": "negative"}.get(
            value, value if value in ("negative", "neutral", "positive") else "neutral")


def raise_to(current: str, target: str) -> str:
    return target if SEVERITY_ORDER[target] < SEVERITY_ORDER[current] else current


def apply_rules(triage: dict, text: str, facts: dict) -> dict:
    drivers = list(triage.get("severity_drivers") or [])
    severity = triage["severity"]
    rule_hits = []
    los_red = facts.get("los_light") == "red" or bool(LOS.search(text) and RED.search(text))
    if (AREA.search(text) or facts.get("area_impact")) and (TOTAL_LOSS.search(text) or facts.get("area_impact")):
        severity = raise_to(severity, "P1")
        rule_hits.append("multi-premises outage")
    if los_red:  # a red LOS light means no optical signal, i.e. total loss of fiber service
        severity = raise_to(severity, "P1")
        rule_hits.append("fiber LOS with total service loss")
    if EMERGENCY.search(text) or facts.get("impact") == "emergency":
        severity = raise_to(severity, "P1")
        rule_hits.append("emergency / safety impact")
    if WORK.search(text) or facts.get("impact") == "work":
        severity = raise_to(severity, "P2")
        rule_hits.append("work or business impact")
    if REPEAT.search(text) and triage["intent"] != "other":
        severity = raise_to(severity, "P2")
        rule_hits.append("recurring issue / already self-troubleshot")
    churn = bool(triage.get("churn_risk")) or bool(CHURN.search(text))
    if CHURN.search(text):
        severity = raise_to(severity, "P2")
        rule_hits.append("cancellation / port-out / regulator threat")
    if facts.get("impact") == "minor" and severity == "P2" and not rule_hits:
        severity = "P3"
        rule_hits.append("customer reports minor impact")
    for hit in rule_hits:
        words = set(re.findall(r"[a-z]{4,}", hit))
        if not any(len(words & set(re.findall(r"[a-z]{4,}", d.casefold()))) >= 2 for d in drivers):
            drivers.append(hit)  # skip rule drivers the model already stated in other words
    return {**triage, "severity": severity, "severity_drivers": drivers, "churn_risk": churn,
            "rules_applied": rule_hits}


class Triager:
    def __init__(self, llm: LLMGateway | None) -> None:
        self.llm = llm

    async def run(self, complaint: str, classes: list[dict], knn: dict[str, float], intake: dict | None,
                  product_hint: str | None, trace_id: str, taxonomy_version: int,
                  neighbours: list[dict] | None = None) -> tuple[dict, dict]:
        """Returns (triage, meta). `complaint` must already be PII-redacted."""
        by_intent = {c["intent"]: c for c in classes}
        posterior = (intake or {}).get("posterior") or {}
        facts = (intake or {}).get("facts") or {}
        meta: dict = {"prompt_version": TRIAGE_VERSION, "degraded": []}
        injection = bool(INJECTION.search(complaint))
        payload = json.dumps({
            "taxonomy": {"version": taxonomy_version, "classes": [
                {"intent": c["intent"], "label": c["label"], "product": c["product"], "description": c["description"]}
                for c in classes]},
            "knn_votes": dict(list(knn.items())[:5]),
            "intake": {"answers": [{"q": a["question"], "a": a.get("answer") or a.get("text")}
                                   for a in (intake or {}).get("answers", [])], "facts": facts},
            "product_hint": product_hint,
        }, ensure_ascii=False)
        user = f"{payload}\n<complaint>\n{complaint[:4000]}\n</complaint>"
        llm_out: dict | None = None
        if self.llm is not None:
            try:
                result = await self.llm.json("triage", TRIAGE_SYSTEM, user, TriageOut, max_tokens=700,
                                             trace_id=trace_id, name="triage")
                llm_out = result.data
                meta.update(model=result.model_id, fallback_path=result.fallback_path, attempts=result.attempts,
                            latency_ms=result.latency_ms)
            except LLMUnavailable as exc:
                meta["degraded"].append("triage_llm_unavailable")
                meta["attempts"] = exc.attempts
        if llm_out is None:
            leader = max(posterior or knn or {"other": 1.0}, key=lambda k: (posterior or knn or {"other": 1.0})[k])
            # Degraded mode: borrow the severity most similar resolved cases of this intent had.
            sev_votes: dict[str, float] = {}
            for n in neighbours or []:
                if n.get("intent") == leader and n.get("severity") in SEVERITY_ORDER:
                    sev_votes[n["severity"]] = sev_votes.get(n["severity"], 0) + (n.get("similarity") or 0)
            severity = max(sev_votes, key=sev_votes.get) if sev_votes else "P3"
            llm_out = TriageOut(intent=leader, product=by_intent.get(leader, {}).get("product", "Unknown"),
                                severity=severity, confidence=0.4,
                                severity_drivers=["severity of similar resolved cases (LLM unavailable)"]
                                if sev_votes else []).model_dump()
            meta["model"] = "rules+knn (degraded)"
        intent = llm_out["intent"] if llm_out["intent"] in by_intent else "other"
        notes = []
        if llm_out["intent"] not in by_intent and llm_out["intent"] != "other":
            notes.append(f"Model label '{llm_out['intent']}' is not in taxonomy v{taxonomy_version}; mapped to other")
        clarify_top = max(posterior, key=posterior.get) if posterior else None
        if clarify_top and posterior[clarify_top] >= 0.8 and clarify_top != intent and clarify_top in by_intent:
            notes.append(f"Customer's intake answers point to '{clarify_top}' "
                         f"(p={posterior[clarify_top]:.2f}); overriding model label '{intent}'")
            intent = clarify_top
        knn_agreement = round(knn.get(intent, 0.0), 3)
        clarify_agreement = round(posterior.get(intent, 0.0), 3) if posterior else None
        llm_conf = float(llm_out.get("confidence", 0.5))
        if clarify_agreement is None:
            confidence = 0.6 * llm_conf + 0.4 * knn_agreement
        else:
            confidence = 0.45 * llm_conf + 0.25 * knn_agreement + 0.30 * clarify_agreement
        if intent == "other":
            confidence = min(confidence, 0.4)
        cls = by_intent.get(intent, {})
        triage = {
            **llm_out, "intent": intent, "intent_label": cls.get("label", "Unclassified issue"),
            "category": cls.get("category", "Unknown"), "area": cls.get("area"),
            "product": cls.get("product") or llm_out.get("product") or product_hint or "Unknown",
            "confidence": round(confidence, 3), "llm_confidence": round(llm_conf, 3),
            "knn_agreement": knn_agreement, "clarify_agreement": clarify_agreement,
            "taxonomy_version": taxonomy_version, "prompt_injection": injection, "notes": notes,
        }
        for tried in facts.get("tried", []):
            if tried != "nothing" and tried not in triage["entities"]["actions_tried"]:
                triage["entities"]["actions_tried"].append(tried)
        if facts.get("impact") and not triage["entities"].get("impact"):
            triage["entities"]["impact"] = facts["impact"]
        triage = apply_rules(triage, complaint + "\n" + " ".join(
            str(a.get("answer") or a.get("text") or "") for a in (intake or {}).get("answers", [])), facts)
        return triage, meta
