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
