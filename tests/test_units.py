"""Unit tests for the decision logic: clarification math, grounding, routing, severity rules, PII, drift."""

from __future__ import annotations

import numpy as np

from telecom_assistant.ai.clarify import (
    bayes_update,
    entropy,
    expected_information_gain,
    likelihood_table,
    load_bank,
)
from telecom_assistant.ai.resolver import route, validate_citations
from telecom_assistant.ai.triage import apply_rules
from telecom_assistant.config import Settings
from telecom_assistant.insights.discovery import agglomerate
from telecom_assistant.insights.drift import psi
from telecom_assistant.knowledge.retrieval import RetrievalResult, knn_votes, recurrence
from telecom_assistant.pii import redact

BANK = {q["id"]: q for q in load_bank()}
BB = ["connectivity.intermittent_drop", "connectivity.slow_speed", "wifi.coverage_or_interference",
      "fiber.loss_of_signal", "connectivity.area_outage"]


def test_entropy_and_information_gain_prefers_discriminating_question():
    uniform = {i: 1 / len(BB) for i in BB}
    assert abs(entropy(uniform) - np.log2(5)) < 1e-9
    gain_symptom = expected_information_gain(uniform, BANK["bb_symptom"])
    gain_billing = expected_information_gain(uniform, BANK["billing_issue"])  # out of scope -> uninformative
    assert gain_symptom > 1.0
    assert gain_billing < 1e-9


def test_likelihoods_are_distributions_and_update_concentrates_posterior():
    table = likelihood_table(BANK["bb_symptom"], BB)
    for intent in BB:
        assert abs(sum(table[o][intent] for o in table) - 1) < 1e-9
    uniform = {i: 1 / len(BB) for i in BB}
    posterior = bayes_update(uniform, BANK["bb_symptom"], ["drops"])
    assert max(posterior, key=posterior.get) == "connectivity.intermittent_drop"
    assert posterior["connectivity.intermittent_drop"] > 0.75
    # an "unsure" answer is equally likely under every intent, so it leaves the posterior unchanged
    neutral = bayes_update(uniform, BANK["neighbours"], ["unsure"])
    assert abs(neutral["connectivity.slow_speed"] - uniform["connectivity.slow_speed"]) < 1e-9


def _sources():
    return [{"id": "KB-1#h1", "kind": "kb", "audience": "customer", "text": "restart router"},
            {"id": "KB-1#c1", "kind": "kb", "audience": "agent", "text": "line test"},
            {"id": "T-1", "kind": "ticket", "audience": "agent", "text": "changed channel"}]


def test_citation_validation_enforces_grounding_and_customer_gate():
    draft = {"customer_steps": [{"text": "Restart the router", "citations": ["KB-1#h1", "FAKE"]},
                                {"text": "Run a line test yourself", "citations": ["KB-1#c1"]},
                                {"text": "Made up", "citations": ["NOPE"]}],
             "agent_steps": [{"text": "Run line test", "citations": ["KB-1#c1"]},
                             {"text": "Offer a refund within 2 days", "citations": ["T-1"]}],
             "probable_root_cause": {"text": "x", "citations": ["NOPE"]}, "customer_message": "We guarantee it"}
    out, warnings = validate_citations(draft, _sources())
    assert [s["text"] for s in out["customer_steps"]] == ["Restart the router"]
    assert out["customer_steps"][0]["citations"] == ["KB-1#h1"]
    agent_texts = [s["text"] for s in out["agent_steps"]]
    assert "Run a line test yourself" in agent_texts  # demoted: not backed by a self-help section
    assert not any("refund" in t for t in agent_texts)
    assert out["probable_root_cause"] is None and out["customer_message"] == ""
    assert out["citation_validity"] == 1.0 and warnings


def _retrieval(sim: float, intent: str, n: int) -> RetrievalResult:
    tickets = [{"id": f"T-{i}", "kind": "ticket", "intent": intent, "similarity": sim, "rerank": sim}
               for i in range(n)]
    return RetrievalResult(tickets=tickets, kb=[{"id": "KB-1#h1", "kind": "kb", "similarity": sim}])


CLASSES = {"wifi.coverage_or_interference": {"label": "Weak Wi-Fi", "sensitive": False},
           "billing.unexpected_charge": {"label": "Charge", "sensitive": True}}


def test_routing_self_service_requires_simple_confident_recurring_and_grounded():
    settings = Settings()
    triage = {"intent": "wifi.coverage_or_interference", "severity": "P3", "confidence": 0.85}
    draft = {"customer_steps": [{"text": "a", "citations": ["KB-1#h1"]}], "citation_coverage": 1.0}
    decision = route(settings, triage, _retrieval(0.8, triage["intent"], 5), draft, CLASSES, None)
    assert decision["route"] == "self_service" and decision["recurrence"] == 5
    assert route(settings, {**triage, "severity": "P2"}, _retrieval(0.8, triage["intent"], 5), draft, CLASSES,
                 None)["route"] == "assisted"
    assert route(settings, {**triage, "severity": "P1"}, _retrieval(0.8, triage["intent"], 5), draft, CLASSES,
                 None)["route"] == "human"
    assert route(settings, triage, _retrieval(0.8, triage["intent"], 1), draft, CLASSES, None)["route"] == "assisted"
    sensitive = {**triage, "intent": "billing.unexpected_charge"}
    assert route(settings, sensitive, _retrieval(0.8, sensitive["intent"], 5), draft, CLASSES, None)["route"] == "human"
    assert route(settings, triage, _retrieval(0.8, triage["intent"], 5), {**draft, "abstain": True}, CLASSES,
                 None)["route"] == "human"
    assert route(settings, {**triage, "prompt_injection": True}, _retrieval(0.8, triage["intent"], 5), draft,
                 CLASSES, None)["route"] == "human"


def test_severity_rules_raise_for_outage_los_work_and_churn():
    base = {"intent": "connectivity.intermittent_drop", "severity": "P3", "severity_drivers": []}
    assert apply_rules(base, "Our whole street has no internet", {})["severity"] == "P1"
    assert apply_rules(base, "fiber box", {"los_light": "red"})["severity"] == "P1"
    assert apply_rules(base, "slow, I work from home", {})["severity"] == "P2"
    churn = apply_rules(base, "fix it or I will cancel my connection", {})
    assert churn["severity"] == "P2" and churn["churn_risk"]
    assert apply_rules({**base, "severity": "P2"}, "a bit slow", {"impact": "minor"})["severity"] == "P3"


def test_pii_redaction():
    text, found = redact("Call me on +91 98765 43210 or mail a.b@example.com, account no ACC-778812, 8 pm daily")
    assert "98765" not in text and "example.com" not in text and "ACC-778812" not in text
    assert "8 pm" in text and set(found) >= {"EMAIL", "PHONE", "ACCOUNT"}


def test_knn_votes_and_recurrence():
    tickets = [{"intent": "a", "similarity": 0.9}, {"intent": "a", "similarity": 0.8}, {"intent": "b", "similarity": 0.5}]
    votes = knn_votes(tickets)
    assert next(iter(votes)) == "a" and abs(sum(votes.values()) - 1) < 1e-3
    assert recurrence(tickets, "a", 0.7) == 2


def test_psi_and_clustering():
    assert psi({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == 0
    assert psi({"a": 0.9, "b": 0.1}, {"a": 0.1, "b": 0.9}) > 0.2
    rng = np.random.default_rng(0)
    base_a, base_b = rng.normal(size=16), rng.normal(size=16)
    vectors = np.stack([base_a + rng.normal(scale=0.05, size=16) for _ in range(4)] +
                       [base_b + rng.normal(scale=0.05, size=16) for _ in range(3)])
    clusters = sorted(agglomerate(vectors, 0.8), key=len, reverse=True)
    assert [len(c) for c in clusters] == [4, 3]


def test_p1_rules_cover_paraphrases():
    base = {"intent": "connectivity.area_outage", "severity": "P2", "severity_drivers": []}
    for text in ["My neighbours are offline too and the whole street seems affected",
                 "Everyone on our street has lost internet, not just my home",
                 "The LOS lamp on the ONT is glowing red and the fibre service is totally down",
                 "Red LOS indicator on the optical box and no fibre service at all",
                 "Our whole street has no brodband since morning"]:
        assert apply_rules(base, text, {})["severity"] == "P1", text
    assert apply_rules({**base, "severity": "P3"}, "my router is not working well", {})["severity"] == "P3"
