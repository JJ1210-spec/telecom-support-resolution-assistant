# Issues and Errors

This log records issues found during the project. Keep the original symptom, attempted methods, final resolution and verification together. Open issues remain visible.

## DATA-001 Duplicate generated complaints — resolved

- **Phase:** 1, synthetic data.
- **Observed:** The first generator pass produced 29 duplicate resolved complaint strings and 5 duplicate unresolved complaint strings.
- **Cause:** A limited set of symptom and context phrases was reused without a case-specific detail.
- **Method tried:** Counted exact `body` strings in each output and inspected the repeated patterns.
- **Resolution:** Added distinct, plausible duration details to indexed complaints and a duplicate assertion to the validator.
- **Verification:** Final generator and validator run passed: all 288 indexed complaint strings are unique.

## DATA-002 Closure evidence absent from resolved records — resolved

- **Phase:** 1, synthetic data.
- **Observed:** Some generated resolution steps ended by escalating or opening an investigation, yet the record was marked resolved.
- **Cause:** The generator reused troubleshooting guidance as if it were a completed historical resolution.
- **Method tried:** Reviewed the resolution steps and status relationship across issue families.
- **Resolution:** Added a closure confirmation step and explicit synthetic closure evidence to every resolved record. Unresolved records keep these fields empty.
- **Verification:** Final validator run passed: all 192 resolved records have steps and closure evidence; all 96 unresolved records have neither.

## DATA-003 Inaccurate edge-case tags — resolved

- **Phase:** 1, synthetic data.
- **Observed:** The generator initially tagged a complaint as containing a typo or abbreviation even when no matching word was changed.
- **Cause:** Tags were appended based on a schedule rather than the mutation result.
- **Method tried:** Compared the generated text with the pre-mutation text.
- **Resolution:** Add a tag only when the corresponding mutation changed the complaint.
- **Verification:** Final generator and validator run passed; spot checks confirmed tags correspond to changed text.

## DEV-001 Git checkout ownership mismatch — resolved

- **Phase:** Repository setup.
- **Observed:** Git rejected the new checkout as having dubious ownership because the sandbox-created `.git` directory was owned by the sandbox account.
- **Cause:** Sandbox and interactive Windows user have different security identifiers.
- **Method tried:** Ran `git status` under the interactive context; it reported the mismatch.
- **Resolution:** Used `git -c safe.directory=<this repository>` for each repository command, avoiding a broad global trust rule.
- **Verification:** Three scoped commits completed successfully.

## DEV-002 Four-billion-parameter model too slow and unusable output — resolved for MVP

- **Phase:** Live integration.
- **Observed:** First live complaint exceeded the client's 240-second limit. With bounded output, a second run took about 152 seconds and neither model stage returned a usable result; retrieved sources were returned safely without a draft.
- **Cause:** The local 4B model was too slow for this CPU configuration. The initial generic warning did not distinguish timeout from invalid JSON, so the precise second-run failure mode remains unverified.
- **Methods tried:** Added per-stage timings and output token bounds; inspected that retrieval completed; probed a 1.7B model directly for valid JSON.
- **Resolution:** Switched the default to Qwen3 1.7B and retained strict JSON validation and retrieval-only degradation. Improved diagnostic warnings without logging complaint text.
- **Verification:** A real red-LOS complaint returned a valid cited draft in about 50 seconds before triage correction and about 60 seconds after correction.

## DEV-003 Missing resolution steps in retrieved ticket text — resolved

- **Phase:** Knowledge service.
- **Observed:** The initial ticket search text contained the complaint and summary but omitted historical resolution steps needed by the draft prompt.
- **Cause:** The source-text builder did not include the steps array.
- **Resolution:** Added resolution steps to the ticket text used for embeddings and returned evidence; reseeded the local corpus.
- **Verification:** The live fiber answer cited retrieved resolved tickets and the KB article, and unit tests still passed.

## DEV-004 Small-model triage mislabeled a red-LOS outage — resolved for this case

- **Phase:** Live integration.
- **Observed:** A red-LOS, no-internet fiber complaint was classified as intermittent Home Broadband at P3.
- **Cause:** The small model's structured classification was accepted without cross-checking the strong retrieved-case agreement or the total-loss cue.
- **Resolution:** Added a conservative top-three agreement check for intent/product and a narrow P1 total-service-loss rule. Adjustments are surfaced in the response.
- **Verification:** The same live complaint returned `fiber.loss_of_signal`, `Fiber`, `P1` and three cited steps. Two targeted rule tests and the full nine-test suite passed.

## DEV-005 Starlette test-client deprecation warning — open

- **Phase:** Automated testing.
- **Observed:** The current FastAPI/Starlette test client warns that its use of `httpx` is deprecated in favor of `httpx2`.
- **Impact:** Tests pass; no runtime failure observed.
- **Next action:** Revisit when the test-client dependency path stabilizes. Do not change production code solely to suppress the warning.

## DEV-006 Ticket ID encoded mutable status — resolved

- **Phase:** Synthetic data and update integration.
- **Observed:** A ticket promoted from unresolved to resolved retained a `-U` suffix in its ID.
- **Cause:** The generator used status-specific ID suffixes even though ticket status can change.
- **Resolution:** Made ticket IDs stable and status-neutral, regenerated the corpus, and seeded a fresh baseline database. The resolved/unresolved distinction remains in the `status` field and service search pool.
- **Verification:** The validator passed; a local update replay moved three records between status pools without changing their IDs.

## DEV-007 Vague complaint received unsupported steps — resolved for this example

- **Phase:** Live integration and evidence gating.
- **Observed:** A complaint with no identifiable symptom returned steps while its model-generated summary said evidence was insufficient.
- **Cause:** The original draft decision relied on citation ID validity and presence of steps; it did not enforce agreement between the summary and steps or a minimum evidence score for drafting.
- **Methods tried:** Measured top-source scores across 48 answerable and 8 abstain development complaints. At a 0.60 score gate, 39 answerable cases would pass and 7 abstain cases would be rejected. These data informed the gate and cannot serve as independent validation.
- **Resolution:** Added a provisional `MIN_DRAFT_SCORE=0.60` check before drafting and withheld steps when a summary explicitly reports insufficient evidence. Added focused tests.
- **Verification:** The same vague complaint returned `insufficient_evidence`, zero steps and top score 0.57538 in 19.7 seconds; a clear fiber outage still returned four cited steps. Broader independent abstention and citation-support evaluation remains open.

## DEV-008 Portal and Assist circular import — resolved

- **Phase:** 5, authenticated portals.
- **Observed:** The new admin account CLI failed when the portal module was imported directly, although API tests passed.
- **Cause:** Portal imported the request schema from Assist, while Assist imported Portal during app setup.
- **Resolution:** Moved the shared `ResolveInput` schema into `schemas.py` and imported it from both services.
- **Verification:** Direct portal module import, `python -m telecom_assistant.portal --help`, the full test suite and lint passed.

## DEV-009 Local Wi-Fi case received over-severe and weakly supported customer advice — resolved for this example

- **Phase:** 5, real-model portal integration.
- **Observed:** A localized weak-Wi-Fi complaint was classified P2, so no customer check appeared. Its draft also invented a duration. A later P3 result produced a customer step justified only by the KB article title.
- **Methods tried:** Inspected saved model output, source scores, stage timings, and the exact KB lines rather than relying on a valid source ID alone.
- **Resolution:** Added a narrow localized-Wi-Fi P3 rule, a conservative three-case intent agreement adjustment, a shorter KB-first draft prompt, summary overconfidence replacement, and a customer gate that matches actionable KB check lines only. The customer sees the KB wording and its supporting excerpt.
- **Verification:** The second live run returned P3 in 55.8 seconds. Reapplying the final gate to the saved real draft yielded two actionable KB checks and rejected the title-only match. Targeted tests and the 17-test suite passed. Broader independent quality validation remains open.

## DEV-010 Real-model latency and availability — mitigated, open

- **Phase:** 5, portal integration.
- **Observed:** The first live portal request took 110.5 seconds, mainly because drafting took 82.2 seconds. A later run took 55.8 seconds after limiting sources and output length; this is still slow for an interactive customer flow.
- **Resolution so far:** Ticket creation is persisted before AI analysis; a failure leaves it in `needs_review`, visible to admins with a retry action. The admin dashboard checks whether Ollama and the Knowledge service are reachable. The prompt uses at most four steps and fewer source passages.
- **Remaining:** Measure latency across many cases; move AI analysis into a durable worker queue, add safe retry/backoff and resource monitoring, and consider a faster local model or capable hardware. Availability cannot be guaranteed if Ollama or the laptop is down.

## P6-001 "Not sure" answers penalised in-scope intents — resolved

- **Phase:** 6, adaptive intake.
- **Observed:** A unit test showed that answering "I'm not sure" to *"Are neighbours also affected?"* lowered the probability of every in-scope intent (slow speed 0.20 → 0.13) and raised out-of-scope ones.
- **Cause:** Neutral options had probability 0.10/Σ for in-scope intents but 1/3 for out-of-scope intents, so a non-answer still counted as evidence.
- **Resolution:** A neutral option now carries a fixed share of probability (0.15) under *every* intent, and the remaining 0.85 is split among the informative options. A non-answer therefore leaves the posterior unchanged.
- **Verification:** `test_likelihoods_are_distributions_and_update_concentrates_posterior` asserts the posterior is unchanged to 1e-9.

## P6-002 Retrieval took 1.6-3.4 s on the hosted stack — resolved

- **Phase:** 6, hosted APIs.
- **Observed:** Live traces showed retrieval at 1.6-3.4 s even when the query embedding was cached.
- **Cause:** Each Qdrant call costs about 350 ms (the cluster is in sa-east-1), and the ticket/KB searches and their two rerank calls ran one after another. Query-embedding cache lookups also went to Neon (about 150 ms).
- **Resolution:** Ticket and KB searches now run concurrently, and so do the two rerank calls. Query embeddings use an in-process LRU; the database hash cache is kept for passages, where it matters for re-indexing cost.
- **Verification:** Measured warm retrieval at 0.75 s (search 0.35 s + rerank 0.39 s).

## P6-003 Gemini drafts failed validation or stopped with RECITATION — resolved

- **Phase:** 6, hosted APIs.
- **Observed:** About half of the draft calls to `gemini-3.5-flash-lite` fell back to Groq, adding 5-15 s. The failures were (a) `agent_steps` returned as plain strings, (b) truncated JSON, and (c) `finishReason: RECITATION` when steps copied KB text.
- **Methods tried:** Passed the Pydantic schema through as `responseJsonSchema` (constrained decoding) and added a "rephrase, don't copy" instruction (`draft@3.1`). That fixed (a), but (b) and (c) still occurred.
- **Resolution:** Kept constrained decoding for Gemini. Content-filter stops now raise a provider error, so the gateway fails over at once instead of retrying the same model. By measurement, the draft chain is now `groq:openai/gpt-oss-120b` first with Gemini as fallback, and triage stays Gemini-first. This is a configuration change only.
- **Verification:** Four consecutive live analyses ran triage and draft on the primary provider: triage 1.3-1.5 s, draft 1.5-2.8 s.

## P6-004 Steps cited a neighbouring KB article — resolved

- **Phase:** 6, grounding.
- **Observed:** A complaint triaged as *weak Wi-Fi in some rooms* received steps from the *intermittent drop* article, because the Wi-Fi article's sections ranked below the drop article for that wording.
- **Resolution:** Parent-document expansion now always loads the published article(s) for the *triaged intent* first, followed by the best retrieved articles (at most 2).
- **Verification:** Re-running the same complaint on the hosted stack gave customer steps citing `KB-BB-WIFI#h1-h3` and agent steps citing `KB-BB-WIFI#c1-c3`. The agent steps also cited `LRN-TCK-2610-39B122`, the case learned from the earlier live ticket, which shows the learning loop end to end.

## P6-005 Rate-limit storm degraded 70% of the first live eval — resolved

- **Phase:** 6, evaluation.
- **Observed:** `reports/eval_20261003_181606.md` (3 concurrent cases) showed `triage_llm_unavailable` 28× and `draft_llm_unavailable` 38×. P1 recall fell to 50% and 54/56 cases routed to humans. When the LLM was reachable, quality was high: intent macro-F1 0.936, 100% citation validity, 100% judged step support, 0 unsafe routes.
- **Cause:** A burst probe confirmed the free-tier limits: Gemini Flash-Lite allows 15 requests/min, and Groq gpt-oss-120b allows 8,000 tokens/min (about two drafts a minute). The gateway treated 429s as failures, so the circuit breakers opened on both providers, and the chains held only two models.
- **Resolution:**
  - per-model sliding-window RPM/TPM limiter (wait at most 6 s, otherwise fail over);
  - 429 → cooldown from Retry-After / retryDelay without tripping the breaker;
  - four-model chains with independent quotas (`gemini-3.1-flash-lite` and `gpt-oss-20b` added);
  - smaller draft prompts (the triaged article in full, other articles only their self-help sections; 3 past cases);
  - degraded triage borrows the severity of the nearest resolved cases;
  - multilingual P1 rules: Hindi/Hinglish "padosi offline", "poori gali", "LOS laal", "एल ओ एस लाल" were missed before, and bare "kaam" was removed from the work-impact cue because "kaam nahi kar raha" means "not working";
  - evals run sequentially by default and also report metrics on the LLM-available subset.
- **Verification:** new tests cover the limiter window maths, the 429 cooldown (breaker stays closed) and the multilingual P1 phrases. The sequential re-run `reports/eval_20261003_183348.md` had a 1.8% degraded rate (was 69.6%), P1 recall 100% (was 50%), intent macro-F1 1.000 and 0 unsafe routes.
