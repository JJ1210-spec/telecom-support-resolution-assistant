# Telecom Support Resolution Assistant

A local-first, two-service assistant for telecom support agents. It classifies a customer complaint, retrieves relevant resolved tickets and knowledge-base articles by meaning, and drafts a cited resolution for agent review. The data and outcomes in this repository are synthetic.

## What runs locally

- **Knowledge service** on port 8001: canonical SQLite records, versioned updates, taxonomy and semantic search. Unresolved cases are kept in a separate logical search pool and cannot be returned as resolution evidence.
- **Assist API** on port 8000: complaint triage, retrieval orchestration, cited draft validation and the agent web interface.
- **Ollama** on port 11434: `embeddinggemma` for multilingual embeddings and `qwen3:4b` for triage and drafting.

The web interface is at `http://127.0.0.1:8000`. OpenAPI documentation is at `/docs` on each service. See [architecture](docs/architecture.md) for the complete flow and current limits.

## Start on Windows

1. Install [Ollama for Windows](https://ollama.com/download/windows), then pull the two local models:

   ```powershell
   ollama pull embeddinggemma
   ollama pull qwen3:4b
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

4. In two terminals, start the services:

   ```powershell
   .venv\Scripts\python -m uvicorn telecom_assistant.knowledge:app --host 127.0.0.1 --port 8001
   ```

   ```powershell
   .venv\Scripts\python -m uvicorn telecom_assistant.assist:app --host 127.0.0.1 --port 8000
   ```

The defaults in [.env.example](.env.example) can be overridden with environment variables. The file is an example, not automatically loaded. The SQLite database is written to `.runtime/knowledge.sqlite3` and is ignored by Git.

## Try a request

Paste a complaint into the web interface, or send:

```powershell
$body = @{ complaint = 'My fiber box has a red LOS light and there is no internet.' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/v1/resolve' -Method Post -ContentType 'application/json' -Body $body
```

The response contains `triage`, retrieved `sources`, cited `steps`, `decision` and `trace_id`. A missing or invalid model draft returns sources with an `insufficient_evidence` decision. An agent must review any draft before using it.

To exercise evolving data after baseline testing, run `.venv\Scripts\python -m telecom_assistant.seed updates`. This applies three unresolved-to-resolved moves, one KB update and one KB deprecation from [the fixture](data/synthetic/v1/update_events.jsonl). Use a fresh database to return to the baseline.

## Tests and repository records

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check src tests scripts
```

The [dataset guide](data/synthetic/v1/README.md) explains schemas and limitations. [Phase records](docs/phases/) explain what was completed and why; [issues and errors](docs/issues-and-errors.md) records problems, remedies and verification. The evaluation set is never indexed. This MVP has no measured claim of production accuracy or throughput yet.
