"""LLM helpers around the ticket lifecycle: agent copilot, resolution summarizer and per-step chat.

All three validate structured output, re-check citations against the evidence actually supplied,
and degrade to a deterministic answer when every provider is down.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..gateways.llm import LLMGateway, LLMUnavailable
from .prompts import (
    COPILOT_SYSTEM,
    COPILOT_VERSION,
    STEP_CHAT_SYSTEM,
    STEP_CHAT_VERSION,
    SUMMARY_SYSTEM,
    SUMMARY_VERSION,
)
from .resolver import UNSUPPORTED_COMMITMENT


class RootCause(BaseModel):
    text: str
    likelihood: Literal["high", "medium", "low"] = "medium"
    citations: list[str] = Field(default_factory=list)


class NextAction(BaseModel):
    text: str
    owner: Literal["agent", "customer", "field"] = "agent"
    citations: list[str] = Field(default_factory=list)


class ClarifyingQ(BaseModel):
    text: str
    options: list[str] = Field(default_factory=list)


class CopilotOut(BaseModel):
    summary: str
    likely_root_causes: list[RootCause] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    clarifying_questions: list[ClarifyingQ] = Field(default_factory=list)
    customer_reply_draft: str = ""
    risk_flags: list[str] = Field(default_factory=list)


class SummaryOut(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    problem: str
    root_cause: str
    resolution_steps: list[str] = Field(min_length=1)
    failed_attempts: list[str] = Field(default_factory=list)
    customer_self_help: list[str] = Field(default_factory=list)
    escalation_criteria: str = ""
    tags: list[str] = Field(default_factory=list)


class StepChatOut(BaseModel):
    reply: str = Field(min_length=1, max_length=1200)
    needs_human: bool = False


def render_sources(sources: list[dict]) -> str:
    lines = []
    for s in sources:
        if s["kind"] == "ticket":
            body = f"{s.get('snippet', '')} | Root cause: {s.get('root_cause')} | Steps: {'; '.join(s.get('steps') or [])}"
        else:
            body = s.get("text", "")
        lines.append(f'<source id="{s["id"]}" type="{s["kind"]}" similarity="{(s.get("similarity") or 0):.2f}">'
                     f"{body[:800]}</source>")
    return "\n".join(lines)


class Copilot:
    def __init__(self, llm: LLMGateway | None) -> None:
        self.llm = llm

    async def brief(self, ticket: dict, timeline: dict, sources: list[dict], trace_id: str) -> dict:
        valid = {s["id"] for s in sources}
        similar = [{"id": s["id"], "title": s["title"], "similarity": s.get("similarity"),
                    "root_cause": s.get("root_cause"), "steps": s.get("steps", [])[:4],
                    "outcome_score": s.get("outcome_score"), "origin": s.get("origin")}
                   for s in sources if s["kind"] == "ticket"][:5]
        base = {"similar_incidents": similar, "prompt_version": COPILOT_VERSION}
        if self.llm is None:
            return {**base, **self._fallback(ticket, timeline, sources), "degraded": True}
        user = (f"<ticket>{json.dumps(self._ticket_view(ticket), ensure_ascii=False)}</ticket>\n"
                f"<timeline>{json.dumps(timeline, ensure_ascii=False)[:6000]}</timeline>\n"
                f"<sources>\n{render_sources(sources)}\n</sources>")
        try:
            result = await self.llm.json("assist", COPILOT_SYSTEM, user, CopilotOut, max_tokens=1400,
                                         trace_id=trace_id, name="copilot")
        except LLMUnavailable:
            return {**base, **self._fallback(ticket, timeline, sources), "degraded": True}
        data = result.data
        for key in ("likely_root_causes", "next_actions"):
            kept = []
            for item in data[key]:
                item["citations"] = [c for c in item.get("citations", []) if c in valid]
                if item["citations"] and not UNSUPPORTED_COMMITMENT.search(item["text"]):
                    kept.append(item)
            data[key] = kept
        if UNSUPPORTED_COMMITMENT.search(data.get("customer_reply_draft", "")):
            data["customer_reply_draft"] = ""
            data["risk_flags"].append("Reply draft removed: contained an unsupported commitment")
        return {**base, **data, "model": result.model_id, "degraded": False}

    @staticmethod
    def _ticket_view(ticket: dict) -> dict:
        triage = ticket.get("triage") or {}
        return {"complaint": ticket.get("complaint_redacted"), "status": ticket.get("status"),
                "reopen_count": ticket.get("reopen_count"),
                "triage": {k: triage.get(k) for k in ("intent", "intent_label", "product", "severity",
                                                      "severity_drivers", "sentiment", "entities")},
                "intake": [{"q": a["question"], "a": a.get("answer") or a.get("text")}
                           for a in (ticket.get("intake") or {}).get("answers", [])]}

    @staticmethod
    def _fallback(ticket: dict, timeline: dict, sources: list[dict]) -> dict:
        failed = {s["text"] for s in timeline.get("steps", []) if s.get("status") == "did_not_work"}
        actions = []
        for s in sources:
            if s["kind"] == "kb" and s.get("audience") == "agent" and s["text"] not in failed:
                actions.append({"text": s["text"].split(":", 1)[-1].strip(), "owner": "agent", "citations": [s["id"]]})
        return {"summary": "LLM copilot unavailable - showing the closest KB checks and incidents.",
                "likely_root_causes": [{"text": s["root_cause"], "likelihood": "medium", "citations": [s["id"]]}
                                       for s in sources if s["kind"] == "ticket" and s.get("root_cause")][:3],
                "next_actions": actions[:4], "clarifying_questions": [], "customer_reply_draft": "",
                "risk_flags": []}


class Summarizer:
    def __init__(self, llm: LLMGateway | None) -> None:
        self.llm = llm

    async def summarize(self, ticket: dict, timeline: dict, trace_id: str | None = None) -> dict:
        worked = [s["text"] for s in timeline.get("steps", []) if s.get("status") == "worked"]
        failed = [s["text"] for s in timeline.get("steps", []) if s.get("status") == "did_not_work"]
        triage = ticket.get("triage") or {}
        fallback = {
            "title": (triage.get("intent_label") or ticket.get("subject") or "Resolved support case")[:200],
            "problem": ticket.get("complaint_redacted", "")[:600],
            "root_cause": (ticket.get("resolution_note") or "See resolution steps")[:400],
            "resolution_steps": worked or [ticket.get("resolution_note") or "Resolved by support agent"],
            "failed_attempts": failed, "customer_self_help": [], "escalation_criteria": "", "tags": [],
            "prompt_version": SUMMARY_VERSION,
        }
        if self.llm is None:
            return {**fallback, "degraded": True}
        user = (f"<ticket>{json.dumps(Copilot._ticket_view(ticket), ensure_ascii=False)}</ticket>\n"
                f"<timeline>{json.dumps(timeline, ensure_ascii=False)[:8000]}</timeline>")
        try:
            result = await self.llm.json("assist", SUMMARY_SYSTEM, user, SummaryOut, max_tokens=900,
                                         trace_id=trace_id, name="summarize")
        except LLMUnavailable:
            return {**fallback, "degraded": True}
        return {**result.data, "prompt_version": SUMMARY_VERSION, "model": result.model_id, "degraded": False}


class StepChat:
    def __init__(self, llm: LLMGateway | None) -> None:
        self.llm = llm

    async def reply(self, step: dict, source_text: str, history: list[dict], question: str, language: str,
                    trace_id: str | None = None) -> dict:
        if self.llm is None:
            return {"reply": "Thanks - a support agent will look at this step and reply here.", "needs_human": True,
                    "degraded": True}
        user = json.dumps({"step": step["text"], "why": step.get("detail"), "source": source_text[:1500],
                           "language": language,
                           "history": [{"from": m["author_role"], "text": m["body"][:400]} for m in history[-6:]],
                           "customer_question": question[:1000]}, ensure_ascii=False)
        try:
            result = await self.llm.json("draft", STEP_CHAT_SYSTEM, user, StepChatOut, max_tokens=400,
                                         temperature=0.2, trace_id=trace_id, name="step_chat")
        except LLMUnavailable:
            return {"reply": "Thanks - a support agent will look at this step and reply here.", "needs_human": True,
                    "degraded": True}
        data = result.data
        if UNSUPPORTED_COMMITMENT.search(data["reply"]):
            data = {"reply": "A support agent will confirm the details for you here shortly.", "needs_human": True}
        return {**data, "prompt_version": STEP_CHAT_VERSION, "model": result.model_id, "degraded": False}
