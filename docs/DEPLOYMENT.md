# Deployment

Resolve Desk builds the React interface into a single Docker image and serves it with FastAPI. The included [Render blueprint](../render.yaml) defines one web service. PostgreSQL, Qdrant, and Redis are external services; a new deployment does not create or populate them automatically.

## Prepare the services

1. Provision a PostgreSQL database and a Qdrant cluster. Upstash Redis is used for shared caches, rate limits, and quota counters. For a single-instance evaluation, the application can fall back to memory when Redis is absent.
2. Set the variables listed in [`.env.example`](../.env.example) on the deployment platform. At minimum, a hosted AI workflow needs `DATABASE_URL`, `QDRANT_URL`, `QDRANT_API_KEY`, `JINA_API_KEY`, and at least one configured LLM provider key (`GEMINI_API_KEY` or `GROQ_API_KEY`). Set both provider keys for failover. Add the Upstash REST URL/token for shared state; Langfuse keys are optional for tracing. Never commit `.env` or credentials.
3. Use HTTPS for the public deployment. Give the application a stable `DATABASE_URL`; the database is the system of record, while the vector index is derived from the saved corpus.

## Deploy the application

1. Connect this repository to Render and create a Blueprint from `render.yaml` on the branch you intend to submit. Supply the secret values requested by the blueprint. Alternatively, deploy the root `Dockerfile` as a Docker web service and set the same environment variables.
2. Wait for the build and startup to finish. Open `/health` for the process check and `/ready` for component status. `/ready` reports database, vector-store, and cache availability; inspect the JSON flags rather than relying only on the HTTP status. Also load the home page to confirm the built frontend is being served.
3. From a trusted machine with **the same hosted environment variables**, install the Python package and run `python -m telecom_assistant.cli seed` once to load the synthetic corpus. Validate the input first with `python scripts/validate_telecom_dataset.py`. The importer can be rerun safely; unresolved cases are stored but are not used as resolved-solution evidence. New corpus files added later also require another seed run.
4. Create an admin with `python -m telecom_assistant.cli create-user --email you@example.com --role admin`; the CLI prompts for a password. For a synthetic demo only, `seed --demo --demo-tickets 6` creates demo accounts and tickets. Do not assume demo accounts exist in a fresh deployment. A customer can register through the site.
5. Sign in as a customer and an admin, submit a test complaint, and check that the ticket reaches the admin queue or offers cited customer-safe steps. Confirm that source detail opens in the admin view and that the customer sees only privacy-safe evidence.

The container reads `PORT` (default `8000`). Application data is held in external services, not the container filesystem. For a local Docker run, create `.env` and use `docker compose up --build`.

## CI and scheduled evaluation

[CI](../.github/workflows/ci.yml) runs lint, Python tests, dataset validation, frontend build, and Docker build on pushes and pull requests. The [nightly workflow](../.github/workflows/nightly.yml) runs hosted evaluation, drift detection, and class discovery on a schedule or through **Actions → nightly → Run workflow**. It requires repository secrets for the hosted database, vector store, Jina, Gemini, Groq, and Upstash endpoints. The workflow checks for missing secrets before running; Langfuse secrets are optional. Its evaluation report is uploaded as a workflow artifact. Repository visibility does not need to change to run it.

## Rollback and data changes

If a deployment fails, redeploy a known working commit through the host's deployment controls, then check `/health`, `/ready`, customer sign-in, and admin sign-in. Reverting the commit on the deployment branch keeps subsequent builds on that code. A code rollback does **not** undo rows seeded into PostgreSQL, Qdrant points, published KB changes, or taxonomy updates. Restore those separately from backups or with a reviewed migration when necessary.

For service failures, check the component flags at `/ready` and the application logs. An unavailable LLM can leave ticket creation working while analysis falls back to an admin route; a missing vector service reduces retrieval capability. See [Architecture](architecture.md) for the degradation paths and known scaling limits.
