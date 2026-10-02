# Phase 3 Assist API and Interface

## Completed

Implemented a FastAPI assist service and a local web interface. The service reads the current taxonomy, asks a local chat model for structured triage, requests similar resolved tickets and KB articles from the knowledge service, asks for a cited draft, validates citation IDs, and returns a suggested resolution or an insufficient-evidence decision. When triage or drafting fails, the service returns a degraded result rather than invented advice.

## Why

The assist service owns decisions shown to agents; the knowledge service owns data and retrieval. The model is used for text interpretation and drafting, while source eligibility, schema validation and citation-ID checks are enforced by code. The interface uses text-only DOM insertion so retrieved content is displayed as text.

## Verification

Focused tests cover valid citations, fabricated citation removal, abstention on weak retrieval, and complaint length validation. Final test and live-model status are recorded after the integration run.

## Limits

A valid citation ID does not prove the cited text supports a step. The score threshold is not calibrated. The trace ID is returned but traces are not persisted. The model may misclassify sentiment or severity. These need a labeled evaluation set and further checks.
