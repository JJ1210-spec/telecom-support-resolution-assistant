"""Shared fixtures: an isolated stack with a deterministic fake LLM, a hash embedder, the local vector
index and SQLite - no network and no API quota (the CI replay mode)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from telecom_assistant.config import Settings
from telecom_assistant.gateways.kv import MemoryKV
from telecom_assistant.gateways.llm import LLMGateway
from telecom_assistant.services import build_services

INTENT_WORDS = {
    "connectivity.intermittent_drop": ["disconnect", "drops", "dropping", "cuts out"],
    "fiber.loss_of_signal": ["los", "fiber box"],
    "connectivity.area_outage": ["whole street", "building", "neighbours"],
    "billing.unexpected_charge": ["charge", "extra"],
    "mobile.data_unavailable": ["mobile data", "mob data"],
}


class FakeLLM:
    """Deterministic provider keyed on the system prompt. Records calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    async def __call__(self, model: str, system: str, user: str, max_tokens: int, temperature: float, **_):
        if self.fail:
            from telecom_assistant.gateways.llm import ProviderError

            raise ProviderError("simulated outage")
        kind = ("triage" if "triage classifier" in system else "draft" if "grounded resolution" in system
                else "copilot" if "senior telecom" in system else "summary" if "knowledge-base record" in system
                else "stepchat" if "ONE troubleshooting step" in system else "judge" if "verify whether" in system
                else "clarify" if "clarifying question" in system else "name" if "NEW telecom" in system else "other")
        self.calls.append(kind)
        if kind == "triage":
            complaint = user.split("<complaint>")[-1].casefold()
            intent = "other"
            for candidate, words in INTENT_WORDS.items():
                if any(w in complaint for w in words):
                    intent = candidate
                    break
            if intent == "other":
                votes = json.loads(user.split("\n<complaint>")[0]).get("knn_votes") or {}
                intent = next(iter(votes), "other")
            return json.dumps({"intent": intent, "product": "Home Broadband", "severity": "P3",
                               "sentiment": "negative", "sentiment_score": -0.4, "confidence": 0.9,
                               "language": "en", "entities": {"actions_tried": []},
                               "evidence": {"intent": complaint[:30]}}), {"input": 10, "output": 10}
        if kind == "draft":
            customer = re.findall(r'id="([^"]+)" type="kb" audience="customer"', user)
            agent = re.findall(r'id="([^"]+)" type="(?:kb|ticket)" audience="agent"', user)
            return json.dumps({
                "probable_root_cause": {"text": "Possible line or Wi-Fi issue", "citations": agent[:1]},
                "customer_steps": [{"text": f"Customer step {i + 1}", "detail": "why", "citations": [cid]}
                                   for i, cid in enumerate(customer[:2])] +
                                  [{"text": "Unsupported step", "citations": ["KB-FAKE#h9"]}],
                "agent_steps": [{"text": "Agent check", "citations": agent[:1]},
                                {"text": "Promise a refund within 2 days", "citations": agent[:1]}],
                "customer_message": "Sorry about this.", "escalate_if": ["persists"], "abstain": False,
                "confidence": 0.8}), {"input": 10, "output": 10}
        if kind == "copilot":
            ids = re.findall(r'<source id="([^"]+)"', user)
            return json.dumps({"summary": "Customer tried steps.", "likely_root_causes": [
                {"text": "Line fault", "likelihood": "high", "citations": ids[:1]}],
                "next_actions": [{"text": "Run line test", "owner": "agent", "citations": ids[:1]}],
                "clarifying_questions": [{"text": "Is the LOS light red?", "options": ["Yes", "No"]}],
                "customer_reply_draft": "We're checking your line.", "risk_flags": []}), {}
        if kind == "summary":
            return json.dumps({"title": "Evening broadband drops fixed by channel change", "problem": "Drops at 8pm",
                               "root_cause": "Wi-Fi interference", "resolution_steps": ["Changed Wi-Fi channel"],
                               "failed_attempts": ["Router restart"], "customer_self_help": ["Restart router"],
                               "escalation_criteria": "Wired drops", "tags": ["wifi"]}), {}
        if kind == "stepchat":
            needs = "agent" in user.casefold() or "human" in user.casefold()
            return json.dumps({"reply": "Hold the power button for 10 seconds.", "needs_human": needs}), {}
        if kind == "judge":
            n = len(json.loads(user))
            return json.dumps({"verdicts": [{"n": i, "supported": True, "reason": "ok"} for i in range(n)]}), {}
        if kind == "clarify":
            candidates = json.loads(user)["candidates"]
            return json.dumps({"text": "Which fits best?", "options": [
                {"label": c["label"], "intent": c["intent"]} for c in candidates]}), {}
        if kind == "name":
            return json.dumps({"intent": "network.new_issue", "label": "New issue", "description": "Emerging cluster",
                               "product": "Home Broadband", "category": "Technical Support", "area": "internet"}), {}
        return json.dumps({"ok": True}), {}


def test_settings(tmp_path: Path, **overrides) -> Settings:
    chain = ["fake:model"]
    values = dict(database_url=f"sqlite:///{(tmp_path / 'app.sqlite3').as_posix()}", vector_backend="local",
                  embed_provider="hash", llm_chain_triage=chain, llm_chain_draft=chain, llm_chain_assist=chain,
                  llm_chain_judge=chain, runtime_dir=tmp_path / "runtime", frontend_dist=tmp_path / "nodist",
                  service_token="test-service-token-0123456789-abcdefghijkl", allowed_origins=[],
                  app_url="http://test")
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def services(tmp_path, fake_llm):
    settings = test_settings(tmp_path)
    kv = MemoryKV()
    llm = LLMGateway(settings, kv, None, providers={"fake": fake_llm})
    svc = build_services(settings, llm=llm, kv=kv, reranker=False)
    from telecom_assistant.seed import bootstrap

    asyncio.run(bootstrap(svc))
    return svc
