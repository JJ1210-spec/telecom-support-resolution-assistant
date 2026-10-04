# Phase 6 Hosted stack, adaptive intake, ticket lifecycle and React console

## Completed and why

The local Ollama prototype was rebuilt on the PRD's all-hosted free-tier stack, with every provider swappable by configuration:

- **LLMs:** Gemini 3.5 Flash-Lite and Groq gpt-oss-120b / qwen3.8.
- **Retrieval:** Jina embeddings v3 and reranker v2.
- **Storage:** Qdrant Cloud with dense + BM25 hybrid search behind aliases, and Neon Postgres as the system of record.
- **Messaging and observability:** Upstash Redis and QStash, Langfuse, Prometheus-format `/metrics`.

Without keys, the same code runs fully offline on SQLite, an in-memory index and a hash embedder. That offline mode is the replay mode used by the test suite and CI.

New capabilities, mapped to the brief:

| Requirement | Implementation |
|---|---|
| Classify; simple/recurring → AI, else → human | Three-way routing (`self_service` / `assisted` / `human`) with recurrence count, confidence and safety blockers; reasons stored and shown |
| Email acknowledgement as a service | Separate notification FastAPI service; transactional outbox; QStash or HTTP or in-process delivery; idempotent; DLQ + replay |
| Update the same ticket; live until solved | State machine with confirm / "still not working" reopen; admin propose → customer confirm; reopen count and history preserved |
| Resolved → summary into the knowledge base | Outbox `learn`: LLM summary of the whole timeline → searchable learned case + KB draft for admin approval; reopen down-weights it |
| AI suggestions for escalated tickets | Copilot: similar incidents, root causes, next actions excluding failed steps, clarifying questions with options, reply draft (all cited) |
| Ask questions to narrow down the problem (MCQ / subjective) | Information-gain question engine over a data-driven bank, "closest issue" fallback, open "details" question |
| Checkbox "worked / didn't work" + side chat per step | Step checklist with outcomes driving auto-escalation, outcome-weighted retrieval and solution drift; per-step grounded chat drawer |
| Customer-admin communication, choices for unclear complaints (Swiggy-like) | Area → issue chips from the live taxonomy + "Something else"; admin questions with quick-reply options; threaded messages |
| Handle data drift | PSI, OOD, centroid shift, other/low-confidence rates, solution drift; alerts with one-click actions; discovery → taxonomy vN+1 |
| Novelty | Information-gain intake, solution-drift monitoring, incident radar, outcome-weighted retrieval, customer-safety citation gate |

## Verification

- **Automated tests:** 27 tests covering unit logic, the end-to-end lifecycle and infrastructure all pass, and Ruff is clean.
- **Frontend:** the Vite production build passes.
- **Live run on the hosted stack:** intake (posterior 0.50 → 0.96 after one question) → ticket → grounded steps → step chat → all steps failed → escalated → copilot → admin quick-reply question → customer answer → proposal → confirmation → learned summary + 4 emails.
- **Evaluation:** `reports/eval_20261003_195637.md` on the 56 English held-out cases: intent macro-F1 1.000, product accuracy 100%, P1 recall 100%, 0 unsafe self-service routes, citation validity 100%, judged step support 100% (33 steps), abstention recall 100%, KB Recall@5 0.979 with hybrid + rerank, degraded rate 0%. Severity macro-F1 is 0.544, below the 0.70 target; the misses are P2/P3 boundary cases.

## Remaining limitations

See `docs/architecture.md` §10.

The project is scoped to English only (see issue P6-006).
