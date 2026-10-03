# Resolve Desk: telecom support resolution assistant

A full-stack telecom support system that works out what is wrong with a few well-chosen questions,
**fixes simple and recurring issues itself** with grounded, cited steps, **sends complex ones to a human**
with an AI copilot, keeps every ticket **open until the customer confirms the fix**, and adds each
confirmed resolution to the knowledge base.

- Backend: Python, FastAPI.
- Frontend: React (JavaScript/JSX) with Vite, styled to the Coinbase-derived design system in `DESIGN-coinbase.md`.
- Hosted services, all on free tiers: Gemini, Groq, Jina, Qdrant Cloud, Neon Postgres, Upstash Redis + QStash,
  Langfuse.
- The same code also runs fully offline.

> **Preparing to explain it? Start with [docs/INTERVIEW_GUIDE.md](docs/INTERVIEW_GUIDE.md).**
>
> **Deploying it online? Follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).**
>
> Architecture, algorithms, scaling and design decisions: **[docs/architecture.md](docs/architecture.md)** ·
> Product requirements: `../docs/PRD.md` · Problem log: [docs/issues-and-errors.md](docs/issues-and-errors.md)

## What it does

| | Customer | Support agent / admin |
|---|---|---|
| **Intake** | Swiggy-style chips (area → issue → "something else"), then 0-3 adaptive questions picked by **expected information gain**, with a live "what we think it is" panel | Sees every answer and how many bits of uncertainty it removed |
| **Routing** | Simple and recurring issues get steps right away. P2 and borderline issues get safe steps **and** a specialist. P1, sensitive or unclear issues go straight to a human. | Each decision lists its reasons: recurrence, confidence, severity drivers, safety blockers |
| **Steps** | Checklist with **Tried - worked / Tried - didn't work** and a **side chat for each step**. All steps failing escalates automatically. | Sees each step's outcome, notes and step chat. Outcomes re-weight retrieval and feed solution-drift alerts. |
| **Conversation** | Thread with **quick-reply choices** when support asks a question | Ask for info with options, reply, add internal notes, propose a fix, resolve with a note |
| **Lifecycle** | Ticket stays live; "still not working" **reopens the same ticket**; rate the support | Queue sorted by severity and SLA; claim; **copilot** shows similar incidents, root causes, next actions (excluding what failed), questions and a reply draft |
| **Email** | Acknowledgement in seconds, then updates at every step | Notification microservice: transactional outbox, QStash, idempotent sends, DLQ replay, rendered email preview |
| **Learning** | Resolution summary on the ticket | The whole process is summarised and indexed as a searchable case immediately; novel fixes become KB drafts for review |
| **Drift** | n/a | PSI, out-of-distribution rate, centroid shift, unclassified rate, **solution drift** (fixes that stopped working), new-class discovery → taxonomy vN+1 |
| **Novelty** | Outage-aware: "known issue in your area" | **Incident radar** groups similar tickets from one area into an incident; resolve once for everyone |

## Quick start (Windows PowerShell, macOS or Linux)

```bash
# 1. Python backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # macOS/Linux: .venv/bin/python
cp .env.example .env                                       # add API keys (all optional; see below)

# 2. Check providers, load the corpus, create demo users + tickets
.venv/Scripts/python -m telecom_assistant.cli check-keys
.venv/Scripts/python -m telecom_assistant.cli seed --demo --demo-tickets 6   # prints the demo password

# 3. Build the React app and run everything on one port
cd frontend && npm ci && npm run build && cd ..
.venv/Scripts/python -m uvicorn telecom_assistant.main:app --port 8000
```

Open `http://localhost:8000`. The demo accounts are `customer@`, `agent@` and `admin@resolvedesk.dev`. Their
password is `DEMO_PASSWORD` in `.env`, or whatever `seed --demo` prints. API docs are at `/docs`.

For UI development, run `npm run dev` in `frontend/` (port 5173, proxied to the API on 8000). Use
`docker compose up --build` to run the API and the notification service as separate containers.

**No keys?** Leave `.env` empty and the system runs offline: SQLite, an in-memory vector index, a deterministic
hash embedder, memory cache, and emails captured in the outbox. AI features need at least one LLM key (Gemini
or Groq). Without one, tickets still save and go to a human.

## Requirements → where they live

| Requirement | Code |
|---|---|
| Classify; AI resolves simple/recurring, humans the rest | `ai/resolver.py::route`, `ai/triage.py` |
| Email acknowledgement **as a service** | `notify/service.py` (FastAPI app), `notify/outbox.py`, `notify/templates.py` |
| Update the same ticket; live until solved | `tickets/lifecycle.py`, `tickets/desk.py` (`customer_confirm`, `_reopen`, `propose_solution`) |
| Resolved → summary into knowledge base | `tickets/desk.py::learn`, `ai/assistants.py::Summarizer`, `knowledge/indexer.py` |
| AI suggestions for escalated tickets | `ai/assistants.py::Copilot`, `tickets/desk.py::refresh_copilot` |
| Questions (MCQ / free text) to narrow the problem | `ai/clarify.py`, `resources/questions.json` |
| Step checkboxes + per-step side chat | `tickets/desk.py::step_feedback/step_chat`, `frontend/src/pages/customer/Tickets.jsx` |
| Customer ↔ agent messages, choices for unclear complaints | `desk.py::agent_message/customer_message`, `NewTicket.jsx`, `Chat.jsx` |
| Data drift | `insights/drift.py`, `insights/discovery.py`, `knowledge/taxonomy.py`, `cli.py reindex` |

## Evaluation and system health

`telecom-assistant eval` runs the 56 held-out cases (never indexed) through the real pipeline. It writes
`reports/eval_<timestamp>.{md,json}` and stores the run, which the admin **System health → Evaluation** tab
displays. It reports:

- triage F1 and P1 recall;
- the retrieval ablation (dense / sparse / hybrid / +rerank);
- clarification accuracy before vs after questions, using an oracle customer;
- unsafe-route count and abstention precision/recall;
- citation validity and LLM-judged step support;
- per-stage latency and degraded-mode rates.

### Latest results ([`reports/eval_20261003_195637.md`](reports/eval_20261003_195637.md), hosted stack, English synthetic held-out set)

| Metric | Result | Target |
|---|---|---|
| Intent macro-F1 | **1.000** | ≥ 0.80 |
| Product accuracy | **100%** | ≥ 90% |
| P1 recall | **100%** | ≥ 95% |
| Severity macro-F1 | 0.544 | ≥ 0.70 (P2/P3 boundary misses) |
| Unsafe self-service routes (P1 or unanswerable) | **0** | 0 |
| Citation validity / LLM-judged step support | **100% / 100%** (33 steps) | 100% / ≥ 90% |
| Abstention precision / recall | 66.7% / **100%** | ≥ 85% recall |
| KB Recall@5: dense / sparse / hybrid / hybrid + rerank | 0.979 / 0.812 / 0.958 / **0.979** | ≥ 0.85 |
| Intent accuracy: complaint only → after adaptive questions | 93.8% → **100%** (3.6 questions, 1.56 bits) | n/a |
| End-to-end latency p50 / p95 | 5.4 s / 27.3 s | ≤ 8 s p95 |
| Degraded-mode rate | **0%** | < 5% |

The p95 latency comes from free-tier per-minute limits (15 RPM Gemini, 8k TPM Groq): the gateway paces calls and
fails over to slower backup models rather than failing (see issue P6-005). Severity is the weakest metric:
critical outages are always caught, but medium vs low priority (P2/P3) is often off by one level.

At runtime:

- `/metrics` exposes Prometheus metrics (scrapeable by Prometheus or Grafana Agent);
- `/ready` checks the database, vector store and cache;
- LLM generations are traced to Langfuse;
- the admin **Health** page shows provider quota meters, circuit breakers, latency, the outbox/DLQ and the email
  previews.

## Tests

```bash
.venv/Scripts/python -m pytest -q          # 27 tests, no network: fake LLM, hash embedder, local index, SQLite
.venv/Scripts/python -m ruff check src tests
cd frontend && npm run build
```

CI (`.github/workflows/ci.yml`) runs lint, tests, dataset validation, the frontend build and a Docker build.
The nightly workflow runs the live eval, a drift snapshot and discovery against the hosted stack.

## Repository layout

```text
src/telecom_assistant/
  api/            FastAPI app (REST + SSE + SPA), auth/RBAC/CSRF
  tickets/        state machine, store, SupportDesk orchestrator, live event bus
  ai/             clarify (information gain), triage, resolver (grounding + routing), copilot/summarizer/step chat, prompts
  knowledge/      indexer (versioned, idempotent), hybrid retriever, taxonomy registry
  gateways/       LLM chain (breaker, quota), Jina embed/rerank (hash cache), Qdrant/local index, Upstash KV
  notify/         notification service, transactional outbox + dispatcher, email templates
  insights/       drift, discovery, incident radar, KPIs
  resources/      taxonomy seed, question bank, customer self-help KB sections
  evaluation.py   eval harness + markdown report
frontend/         React + Vite, JavaScript/JSX (customer portal and agent/admin console)
data/synthetic/v1 synthetic corpus, held-out eval cases, update events
docs/             architecture, phase records, issues log
```

## Data and privacy
The data is original synthetic telecom data (see `data/synthetic/v1/README.md`). Complaints are PII-redacted
before any LLM, embedding, trace or log call. Free-tier providers may use submitted data, so real customer data
must not be used without paid, no-training agreements. Never share passwords, OTPs or full card numbers in
tickets.
