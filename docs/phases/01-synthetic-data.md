# Phase 1 Synthetic Telecom Data

## What was completed

Created a deterministic, original synthetic data generator and five JSONL outputs: resolved tickets, unresolved tickets, KB articles, a held-out evaluation set and update events. The data covers 16 telecom issue families with English complaints, paraphrases, missing optional fields, typos, abbreviations, ambiguous cases and prompt-injection text. Added a manifest and validation script.

## Why these choices were made

The referenced Tobi-Bueck dataset supplies useful ticket fields and complaint style, but its admin `answer` is not a verified resolution and many rows are not telecom cases. This phase uses its field structure as inspiration and writes new telecom records. Resolved and unresolved cases are separate files to make the initial retrieval boundary obvious. They still use one canonical ticket schema so a real ticket can move from unresolved to resolved without changing identity or losing its history.

JSONL preserves arrays of resolution steps and KB references without packing them into CSV cells. A separate evaluation file prevents the demo from indexing its test complaints. Status and closure are synthetic labels for controlled testing; they do not imply observed real-world outcomes.

## Validation

Ran `python scripts/generate_telecom_dataset.py` and `python scripts/validate_telecom_dataset.py` using the bundled Python runtime. Both completed successfully. The final output has 192 resolved tickets, 96 unresolved tickets, 16 KB articles, 56 evaluation cases and 5 update events. The validator checked unique IDs and complaint strings, source references, resolution data only on resolved cases, expected abstention on unknown cases, and no exact overlap between indexed complaints and evaluation complaints. It found 8 expected-abstention cases, 80 training tickets with a missing product hint, and 48 with a missing subject.

All 288 indexed tickets and all 56 held-out cases are in English. Each issue has three held-out wordings (one reserved wording and two paraphrases) that never appear in the indexed corpus. A sample record from each file was inspected after generation. This is a structural and content spot-check, not an LLM-quality evaluation.

## Known limitations and next phase

The issue-family templates create shared patterns across records and evaluation cases. The same closure sentence appears in many resolved cases, and labels are generated from scenario definitions. The dataset is suitable for an MVP and failure testing, not for a production accuracy claim. Phase 2 should import only resolved cases and published KB into the resolution index, keep unresolved cases out of the answer evidence path, and verify an unresolved-to-resolved status transition using the separate update-event fixture.
