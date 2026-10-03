# Telecom Support Resolution Assistant

A local-first telecom support system with separate customer and admin portals. Customers raise tickets, track status, and see basic checks only when local AI analysis and KB evidence pass a conservative gate. Admins see all incoming tickets, full model output, source passages, analysis history, feedback, service health, and can update status or retry analysis. The historical data and outcomes in this repository are synthetic.

## What runs locally

- **Knowledge service** on port 8001: canonical SQLite records, versioned updates, taxonomy and semantic search. Unresolved cases are kept in a separate logical search pool and cannot be returned as resolution evidence. Its API requires a locally generated service token.
- **Assist and portal service** on port 8000: complaint triage, retrieval orchestration, customer/admin accounts, session and role checks, tickets, feedback, analysis history, and two web interfaces. Direct model-analysis API calls also require the service token.
- **Ollama** on port 11434: `embeddinggemma` for multilingual embeddings and `qwen3:1.7b` for triage and drafting on a CPU laptop.

The sign-in page is at `http://127.0.0.1:8000/login`. Customer accounts self-register. An admin account is created through a local CLI command; the public registration endpoint cannot assign the admin role. OpenAPI documentation is at `/docs` on each service. See [architecture](docs/architecture.md) for the complete flow and current limits.

## Start on Windows

1. Install [Ollama for Windows](https://ollama.com/download/windows), then pull the two local models:

   ```powershell
   ollama pull embeddinggemma
   ollama pull qwen3:1.7b
   ```

2. Create a Python 3.11+ environment from the project directory:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -e '.[dev]'
   ```

3. Seed the synthetic corpus. This runs embeddings locally and may take several minutes on CPU:

   ```powershell
   .venv\Scripts\python -m telecom_assistant.seed bootstrap
   ```

4. Create an admin account. The command prompts privately for a password of at least 12 characters:

   ```powershell
   .venv\Scripts\python -m telecom_assistant.portal create-admin --email you@example.com
   ```

5. In two terminals, start the services:

   ```powershell
   .venv\Scripts\python -m uvicorn telecom_assistant.knowledge:app --host 127.0.0.1 --port 8001
   ```

   ```powershell
   .venv\Scripts\python -m uvicorn telecom_assistant.assist:app --host 127.0.0.1 --port 8000
   ```

The defaults in [.env.example](.env.example) can be overridden with environment variables. The file is an example, not automatically loaded. The knowledge database, portal database, and shared local service token are written under `.runtime/` and ignored by Git. Start both services from the same project directory so they read the same service token. Bind them to localhost unless a proper HTTPS reverse proxy and deployment security are added.

## Try the portals

Open `/login`. Create a customer account, submit a complaint, and wait for local analysis. A ticket is saved first, so it remains visible even if Ollama is unavailable. Customer-facing checks are limited to P3/P4 cases and are rendered from matching published KB check lines selected by the model draft. If evidence is weak, the customer sees that support will review the ticket. Sign out and use the CLI-created admin account to open `/admin`, inspect full ticket details, set status and customer-visible notes, or retry analysis.

Every ticket and admin API checks the logged-in role on the server. Browser mutations also require the session's CSRF token. The old unauthenticated `/v1/resolve` flow is now an internal service endpoint. The provisional draft gate is `MIN_DRAFT_SCORE=0.60`; it can withhold a draft for an answerable complaint. Customer-visible matching is an additional conservative heuristic, not proof of factual entailment. An agent must review any draft before using it.

To exercise evolving data after baseline testing, run `.venv\Scripts\python -m telecom_assistant.seed updates`. This applies three unresolved-to-resolved moves, one KB update and one KB deprecation from [the fixture](data/synthetic/v1/update_events.jsonl). Use a fresh database to return to the baseline.

## Tests and repository records

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check src tests scripts
```

The [dataset guide](data/synthetic/v1/README.md) explains schemas and limitations. [Phase records](docs/phases/) explain what was completed and why; [issues and errors](docs/issues-and-errors.md) records problems, remedies and verification. The evaluation set is never indexed, but was used to inspect retrieval scores, so its [report](reports/retrieval_eval.json) is a development baseline, not a final test. This is a localhost prototype: there is no production accuracy or uptime guarantee, and real customer data must not be used until deployment controls and a privacy review are completed.
