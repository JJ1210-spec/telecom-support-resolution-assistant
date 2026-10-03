# Phase 4 Integration and Initial Evaluation

## Completed

Installed the local Ollama runtime and pulled `embeddinggemma` and two candidate chat models. Seeded the synthetic corpus into the knowledge service, started both FastAPI services locally, exercised a real complaint end to end, and replayed the five update events. Added a retrieval evaluator and a JSON baseline report.

## Why the model changed

The 4B chat model was too slow for a useful CPU demo. A real complaint took about 152 seconds with bounded output, and neither model stage returned a usable result; the system safely returned retrieved sources without a draft. The initial generic error report did not distinguish timeout from invalid JSON. A smaller 1.7B model produced valid JSON and a cited draft on the same complaint in about 50 seconds. The raw model triage mislabeled a clear red-LOS outage, so conservative retrieved-case agreement and explicit total-loss rules were added. The corrected request returned the expected fiber intent, product and P1 severity in about 60 seconds. The default is now `qwen3:1.7b`.

## Measured baseline

On 48 answerable synthetic development complaints, the correct KB was first for 36 cases (Recall@1 = 0.75) and in the top three for 44 (Recall@3 = 0.9167). The four top-three misses were E-MOB-CALL-02, E-PLAN-RENEW-03, E-BB-DROP-02 and E-MOB-CALL-03. The evaluation file was not indexed. It was subsequently used to inspect score thresholds, so it is a development set, not an untouched final test set.

The score analysis shows the tension in a simple global threshold: at 0.60, 39/48 answerable cases clear the draft gate and 7/8 abstain cases fall below it. This does not measure final answer correctness, and the same development cases informed the threshold. A fresh vague complaint initially received steps despite a summary saying evidence was insufficient. A 0.60 draft gate and summary/steps consistency check fixed this example: it returned `insufficient_evidence`, zero steps, top score 0.57538, in 19.7 seconds. A clear red-LOS complaint still returned `fiber.loss_of_signal`, Fiber, P1 and four cited steps in 52.9 seconds on the baseline database. These are individual live checks, not latency or accuracy distributions.

Eleven focused tests passed after these fixes. FastAPI's test client emitted one upstream deprecation warning concerning Starlette and `httpx`; it did not affect the test outcomes. A broader independently authored, labeled test of intent, severity, sentiment, citation support and abstention remains open.

## Remaining gaps to prioritize

1. Citation IDs are valid, but factual support for each step has not been checked independently.
2. The 1.7B model still takes roughly one minute for this example on CPU. Stage timing needs a multi-case distribution before setting a latency target.
3. Paraphrased and typo-heavy queries account for several retrieval misses.
4. The score threshold and triage correction threshold were chosen on development examples, not calibrated on an independent human-labeled set. The 0.60 gate can withhold drafts for some answerable complaints.
5. No authentication, PII redaction, persistent traces, feedback, drift monitor or scalable vector index is present yet.
