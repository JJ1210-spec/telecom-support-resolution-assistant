# Phase 3 Assist API and Interface

## Completed

Implemented a FastAPI assist service and a local web interface. The service reads the current taxonomy, asks a local chat model for structured triage, requests similar resolved tickets and KB articles from the knowledge service, cross-checks strong retrieved-case agreement and narrow severity rules, asks for a cited draft, validates citation IDs, and returns a suggested resolution or an insufficient-evidence decision. When triage or drafting fails, the service returns a degraded result rather than invented advice.

## Why

The assist service owns decisions shown to agents; the knowledge service owns data and retrieval. The model is used for text interpretation and drafting, while source eligibility, schema validation and citation-ID checks are enforced by code. The interface uses text-only DOM insertion so retrieved content is displayed as text.

## Verification

Focused tests cover valid citations, fabricated citation removal, abstention on weak retrieval, complaint length validation, status pool separation, summary/steps consistency, and conservative triage correction. Eleven tests passed at the latest checkpoint. A live red-LOS complaint returned four cited steps, with intent `fiber.loss_of_signal`, product `Fiber` and severity `P1`. The correction rules were reported in `warnings` for transparency. Stage timings were approximately 11.8 s triage, 0.3 s retrieval and 40.7 s drafting, for 52.9 s total on the local CPU laptop. A vague complaint returned no steps under the provisional 0.60 draft gate.

## Limits

A valid citation ID does not prove the cited text supports a step. The score threshold is development-tuned rather than independently calibrated. The trace ID is returned but traces are not persisted. The small model initially misclassified the red-LOS complaint before a narrow correction rule was added. Broader severity and sentiment evaluation is still needed.
