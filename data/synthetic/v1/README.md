# Synthetic Telecom Support Dataset v1

This is an **original synthetic** dataset for the telecom resolution assistant. The [Tobi-Bueck customer-support-tickets dataset](https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets) informed the basic ticket shape (`subject`, `body`, `type`, `queue`, `priority`, `language`, tags and an agent answer). No source rows or answers were copied. The reference dataset has a CC BY-NC 4.0 license; this generated dataset is a separate original artifact and does not inherit a verified real-world outcome from it.

## Files and intended use

| File | Role | Search as resolution evidence? |
| --- | --- | --- |
| `resolved_tickets.jsonl` | Synthetic historical cases with documented steps and closure confirmation | Yes, after importer validation |
| `unresolved_tickets.jsonl` | Open cases for triage, issue discovery, and testing the status boundary | No |
| `kb_articles.jsonl` | Synthetic telecom troubleshooting guidance | Yes, while `status=published` |
| `eval_cases.jsonl` | Held-out complaint inputs with expected labels and relevant KB IDs | Never index |
| `update_events.jsonl` | Later ticket resolution and KB update/deprecation events | Apply only in a dedicated update test |
| `manifest.json` | Counts, generator seed, intents and language distribution | No |

Every line of a `.jsonl` file is one UTF-8 JSON object. IDs are stable for this generator version and do not encode mutable status. Use separate search indexes for resolved cases and unresolved cases if both need similarity search, while keeping a single authoritative record store and an explicit status transition. Never let unresolved cases supply a proposed fix or a citation for a resolution step.

## Ticket fields

`ticket_id`, `subject`, `body`, `type`, `queue`, `priority`, `language`, and `tags` mirror the useful structure of the reference dataset. Added fields are `product_hint`, `device_model`, `location_region`, `error_code`, `intent`, `category`, `product`, `severity`, `sentiment`, `status`, `created_at`, `resolved_at`, `resolution_summary`, `resolution_steps`, `resolution_outcome`, `closure_evidence`, `kb_reference_ids`, `record_version`, `source`, and `edge_case_tags`.

`product_hint` and `subject` can be missing even when the expected product and intent labels exist. `device_model`, `location_region`, and `error_code` are often absent. `resolution_*` fields and `closure_evidence` are null or empty for unresolved cases. Status and closure labels are **synthetic scenario truth**, not observed customer outcomes. In a real system, they must come from a verified ticket lifecycle.

## KB and evaluation fields

Each KB article has `kb_id`, `title`, `product`, `intent`, `status`, `article_version`, `updated_at`, `summary`, `checks`, and `escalation_criteria`. A KB check is conditional guidance, not a guarantee of the root cause.

Evaluation rows have `case_id`, `complaint`, `language`, `product_hint`, `expected_intent`, `expected_category`, `expected_product`, `expected_severity`, `expected_sentiment` where known, `relevant_kb_ids`, `should_abstain`, and `edge_case_tags`. The 8 `other` cases are deliberately ambiguous, outside the known taxonomy, or request unsupported commitments. Evaluate them separately from answerable cases. The evaluation set has unseen wording, but shares the 16 intent families with the indexed corpus; it tests paraphrase handling, **not** new-class generalization.

## Coverage and limits

The data covers 16 issue families across home broadband, fiber, mobile, SIM/eSIM, billing, plan renewal, porting and TV. All complaints are in English and include paraphrases, typos, abbreviations, prior troubleshooting, missing subject/product hint/device model, and insufficient-evidence cases. All names, accounts, locations and outcomes are fictional.

The generator uses a limited number of issue archetypes and many variations of them. As a result, a model may score well by learning the archetypes. Do not use this dataset alone to claim production accuracy. Before making that claim, obtain independently authored, human-reviewed evaluation cases and real-world outcome data under appropriate permissions. In particular, severity labels reflect these scenario templates, and the dataset does not measure real incident prevalence, class drift, or ticket volumes.

## Reproduce and validate

From the project root, run `python scripts/generate_telecom_dataset.py`, then `python scripts/validate_telecom_dataset.py`. The generator uses seed `20261002`. Re-running it overwrites the four generated JSONL files and manifest with deterministic content. The validator checks identifiers, status boundaries, source references, languages, duplicates and exact train/evaluation separation.
