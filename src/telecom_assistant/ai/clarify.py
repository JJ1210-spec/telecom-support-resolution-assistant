"""Adaptive intake: ask the fewest questions that most reduce uncertainty about the issue.

How it works (the 30-second version):
1. Keep a probability for every known issue type ("intent"), e.g. {wifi: 0.5, slow_speed: 0.3, ...}.
2. Start from the customer's chip choice and from the issues of the most similar past tickets (k-NN).
3. For every unasked question in the bank, compute how much it would reduce uncertainty on average
   (expected information gain) and ask the best one:

       IG(q) = H(P) - sum_o P(o) * H(P | o)      H = Shannon entropy, o = an answer option

4. Update the probabilities with Bayes' rule after each answer.
5. Stop when one issue is >= 80% likely, the question budget is used, or no question helps.

Questions and their answer likelihoods live in `resources/questions.json` (data, not code). If the complaint
is unclear, the first question is a Swiggy-style "Which of these is closest?" built from the top candidates,
plus "Something else". A free-text "details" question is the open-ended fallback.
"""

from __future__ import annotations

import json
import math
from importlib import resources

from ..config import Settings
from ..knowledge.retrieval import Retriever, knn_votes
from ..knowledge.taxonomy import TaxonomyRegistry
from ..pii import redact_text

MIN_GAIN = 0.08       # bits; a question must remove at least this much uncertainty to be worth asking
NEUTRAL_MASS = 0.15   # probability mass given to "not sure" answers under every intent
OTHER = "other"


def load_bank() -> list[dict]:
    raw = resources.files("telecom_assistant.resources").joinpath("questions.json").read_text(encoding="utf-8")
    return json.loads(raw)["questions"]


def entropy(dist: dict[str, float]) -> float:
    return -sum(p * math.log2(p) for p in dist.values() if p > 0)


def normalize(dist: dict[str, float]) -> dict[str, float]:
    total = sum(dist.values())
    if total <= 0:
        return {k: 1 / len(dist) for k in dist} if dist else {}
    return {k: v / total for k, v in dist.items()}


def likelihood_table(question: dict, intents: list[str]) -> dict[str, dict[str, float]]:
    """P(answer option | intent). In-scope intents favour the options that list them; others are uninformative."""
    options = question.get("options") or []
    scope = set(question.get("scope") or [])
    table: dict[str, dict[str, float]] = {o["id"]: {} for o in options}
    neutral = [o for o in options if o.get("neutral")]
    informative = [o for o in options if not o.get("neutral")]
    # A neutral answer ("not sure") is equally likely whatever the true issue is, so it carries no evidence.
    p_neutral = NEUTRAL_MASS / len(neutral) if neutral else 0.0
    mass = 1.0 - NEUTRAL_MASS if neutral else 1.0
    for intent in intents:
        for option in neutral:
            table[option["id"]][intent] = p_neutral
        if intent in scope:
            raw = {o["id"]: 1.0 if intent in (o.get("likely") or {}) else 0.04 for o in informative}
        else:
            raw = {o["id"]: 1.0 for o in informative}
        total = sum(raw.values()) or 1.0
        for oid, value in raw.items():
            table[oid][intent] = mass * value / total
    return table


def expected_information_gain(posterior: dict[str, float], question: dict) -> float:
    if question.get("type") == "text" or not question.get("options"):
        return 0.0
    table = likelihood_table(question, list(posterior))
    expected_entropy = 0.0
    for likelihood in table.values():
        p_option = sum(posterior[i] * likelihood.get(i, 0.0) for i in posterior)
        if p_option > 0:
            after = normalize({i: posterior[i] * likelihood.get(i, 0.0) for i in posterior})
            expected_entropy += p_option * entropy(after)
    return max(0.0, entropy(posterior) - expected_entropy)


def bayes_update(posterior: dict[str, float], question: dict, option_ids: list[str]) -> dict[str, float]:
    if question.get("type") != "single" or not option_ids:
        return posterior
    likelihood = likelihood_table(question, list(posterior)).get(option_ids[0])
    if likelihood is None:
        return posterior
    return normalize({i: posterior[i] * likelihood.get(i, 0.0) for i in posterior})


class ClarifyEngine:
    def __init__(self, settings: Settings, registry: TaxonomyRegistry, retriever: Retriever,
                 bank: list[dict] | None = None) -> None:
        self.settings, self.registry, self.retriever = settings, registry, retriever
        self.bank = bank if bank is not None else load_bank()

    def _bank_question(self, question_id: str) -> dict:
        return next(q for q in self.bank if q["id"] == question_id)

    # ------------------------------------------------------------------ prior
    def _candidates(self, area: str | None) -> dict[str, dict]:
        classes = self.registry.by_intent()
        if area:
            scoped = {i: c for i, c in classes.items() if c["area"] == area}
            if scoped:
                return scoped
        return classes

    async def _knn_prior(self, text: str, candidates: dict[str, dict]) -> tuple[dict[str, float], float]:
        """Intent distribution of the 10 most similar resolved tickets, and the best similarity."""
        if not text.strip():
            return {}, 0.0
        result = await self.retriever.search(redact_text(text), top_tickets=10, top_kb=1, rerank=False)
        votes = {i: p for i, p in knn_votes(result.tickets, k=10).items() if i in candidates}
        return (normalize(votes) if votes else {}), result.top_similarity

    def _prior(self, candidates: dict[str, dict], knn: dict[str, float], top_sim: float,
               chosen_intent: str | None) -> dict[str, float]:
        uniform = {i: 1 / len(candidates) for i in candidates}
        if chosen_intent and chosen_intent in candidates:  # the customer tapped a specific issue chip
            return normalize({i: (0.85 if i == chosen_intent else 0.15 / max(1, len(candidates) - 1))
                              for i in candidates})
        if not knn:
            return uniform
        # trust past tickets less when the complaint looks unlike anything we've seen (out of distribution)
        weight = 0.75 if top_sim >= self.settings.ood_similarity else 0.35
        return normalize({i: (1 - weight) * uniform[i] + weight * knn.get(i, 0.0) for i in candidates})

    # ------------------------------------------------------------------ question selection
    def _closest_question(self, posterior: dict[str, float]) -> dict:
        classes = self.registry.by_intent()
        top = sorted(posterior.items(), key=lambda kv: -kv[1])[:5]
        options = [{"id": intent, "label": classes[intent]["label"], "likely": {intent: 1}}
                   for intent, _ in top if intent in classes]
        options.append({"id": OTHER, "label": "Something else", "likely": {}, "neutral": True})
        return {"id": "closest", "type": "single", "kind": "diagnostic", "generated": True,
                "text": "Which of these is closest to your issue?",
                "scope": [o["id"] for o in options if o["id"] != OTHER], "options": options}

    def _next_question(self, state: dict) -> dict | None:
        asked = set(state["asked"])
        posterior = state["posterior"]
        top_p = max(posterior.values()) if posterior else 0.0
        budget = self.settings.clarify_max_questions
        diagnostic_count = sum(1 for a in state["answers"] if a.get("kind") == "diagnostic")
        if len(state["answers"]) >= budget + 1:  # hard cap: 3 diagnostic + 1 context question
            return None
        if state.get("chose_other") and "details" not in asked:
            return self._bank_question("details")
        if top_p < self.settings.clarify_target_posterior and diagnostic_count < budget:
            unclear = top_p < 0.35 and not state.get("area") and not state.get("chosen_intent")
            if unclear and "closest" not in asked:
                return self._closest_question(posterior)
            ranked = sorted(((expected_information_gain(posterior, q), q) for q in self.bank
                             if q["kind"] == "diagnostic" and q["id"] not in asked), key=lambda x: -x[0])
            if ranked and ranked[0][0] >= MIN_GAIN:
                return {**ranked[0][1], "expected_gain": round(ranked[0][0], 3)}
            if "details" not in asked:  # nothing in the bank helps: ask an open question
                return self._bank_question("details")
        if "impact" not in asked and "impact" not in state.get("facts", {}):
            return self._bank_question("impact")
        if "tried" not in asked:
            return self._bank_question("tried")
        return None

    def _view(self, state: dict) -> dict:
        classes = self.registry.by_intent()
        top = sorted(state["posterior"].items(), key=lambda kv: -kv[1])[:4]
        return {
            **state,
            "candidates": [{"intent": i, "label": classes.get(i, {}).get("label", i), "probability": round(p, 3)}
                           for i, p in top],
            "entropy_bits": round(entropy(state["posterior"]), 3),
        }

    # ------------------------------------------------------------------ public API
    async def start(self, complaint: str, area: str | None = None, chosen_intent: str | None = None) -> dict:
        candidates = self._candidates(area)
        knn, top_sim = await self._knn_prior(complaint, candidates)
        prior = self._prior(candidates, knn, top_sim, chosen_intent)
        state = {"complaint": complaint, "area": area, "chosen_intent": chosen_intent, "prior": prior,
                 "posterior": dict(prior), "asked": [], "answers": [], "facts": {}, "top_similarity": top_sim,
                 "knn": knn, "chose_other": False}
        state["next_question"] = self._next_question(state)
        state["done"] = state["next_question"] is None
        return self._view(state)

    async def answer(self, state: dict, question: dict, option_ids: list[str] | None = None,
                     text: str | None = None) -> dict:
        option_ids = option_ids or []
        state = {k: v for k, v in state.items() if k not in ("candidates", "entropy_bits")}
        labels = [o["label"] for o in question.get("options", []) if o["id"] in option_ids]
        for option in question.get("options", []):  # answers can carry facts, e.g. {"impact": "work"}
            if option["id"] in option_ids:
                for key, value in (option.get("facts") or {}).items():
                    if key == "tried":
                        state["facts"].setdefault("tried", []).append(value)
                    else:
                        state["facts"][key] = value
        if question["id"] == "closest" and OTHER in option_ids:
            state["chose_other"] = True
        before = entropy(state["posterior"])
        state["posterior"] = bayes_update(state["posterior"], question, option_ids)
        if text and text.strip():  # free text: re-run the k-NN vote with the extra detail
            state["facts"]["details"] = text.strip()[:1000]
            knn, top_sim = await self._knn_prior(f"{state['complaint']}\n{text}", state["posterior"])
            if knn:
                state["posterior"] = normalize({i: 0.6 * p + 0.4 * knn.get(i, 0.0)
                                                for i, p in state["posterior"].items()})
                state["top_similarity"] = max(state["top_similarity"], top_sim)
        state["asked"].append(question["id"])
        state["answers"].append({"question_id": question["id"], "question": question["text"],
                                 "kind": question.get("kind"), "generated": bool(question.get("generated")),
                                 "type": question.get("type"), "option_ids": option_ids, "answer": labels or None,
                                 "text": text, "information_gain_bits": round(before - entropy(state["posterior"]), 3)})
        state["next_question"] = self._next_question(state)
        state["done"] = state["next_question"] is None
        return self._view(state)

    @staticmethod
    def enriched_complaint(state: dict) -> str:
        """Complaint plus structured answers: a richer query for triage and retrieval."""
        lines = [state.get("complaint", "").strip()]
        for answer in state.get("answers", []):
            value = ", ".join(answer["answer"]) if answer.get("answer") else (answer.get("text") or "")
            if value:
                lines.append(f"Q: {answer['question']} A: {value}")
        return "\n".join(line for line in lines if line)

    @staticmethod
    def leading_intent(state: dict) -> tuple[str | None, float]:
        posterior = state.get("posterior") or {}
        if not posterior or state.get("chose_other"):
            return None, 0.0
        intent, p = max(posterior.items(), key=lambda kv: kv[1])
        return intent, round(p, 3)
