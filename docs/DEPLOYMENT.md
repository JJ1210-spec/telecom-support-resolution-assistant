# Deploying Resolve Desk online (free)

The app is a **single Docker container**: the FastAPI backend, the built React website and the email service
(mounted at `/notify`). The databases are already hosted (Neon Postgres, Qdrant Cloud, Upstash), so deploying
only means running that one container somewhere public.

**Recommended host: [Render](https://render.com)**. The free plan needs no credit card and builds straight from
GitHub using the `Dockerfile` and `render.yaml` in this repo.

---

## Before you start (5 minutes)

1. **Merge the pull request** into `main`:
   https://github.com/JJ1210-spec/telecom-support-resolution-assistant/pull/1 → **Merge pull request** →
   **Confirm merge**. (You can also deploy the `feature/resolve-desk-v1` branch directly.)
2. **Keep your `.env` file open.** You'll copy values from it. Never commit it.
3. **The data is already loaded.** The knowledge base, past tickets and demo accounts are already in your Neon
   database and Qdrant cluster, and the deployed app uses the same ones. You do **not** need to seed again.

---

## Option A: Render Blueprint (recommended)

1. Go to https://dashboard.render.com and **sign in with GitHub**.
2. Click **New +** → **Blueprint**.
3. Select the repository **JJ1210-spec/telecom-support-resolution-assistant** (grant Render access if asked) and
   the branch (`main`, or `feature/resolve-desk-v1` if you didn't merge).
4. Render reads `render.yaml` and shows one service, **resolve-desk**, plus a form for the secret values. Paste
   each one from your `.env`:

   | Key | Where to copy it from |
   |---|---|
   | `GEMINI_API_KEY` | `.env` |
   | `GROQ_API_KEY` | `.env` |
   | `JINA_API_KEY` | `.env` |
   | `QDRANT_URL` | `.env` |
   | `QDRANT_API_KEY` | `.env` |
   | `DATABASE_URL` | `.env` (the full Neon connection string) |
   | `UPSTASH_REDIS_REST_URL` | `.env` |
   | `UPSTASH_REDIS_REST_TOKEN` | `.env` |
   | `LANGFUSE_PUBLIC_KEY` | `.env` (optional) |
   | `LANGFUSE_SECRET_KEY` | `.env` (optional) |
   | `APP_URL` | For now enter `https://resolve-desk.onrender.com`. Fix it in step 7 if your URL differs. |

   `SERVICE_TOKEN` is generated automatically.
5. Click **Apply**. Render builds the Docker image, which takes about 5–8 minutes the first time. Watch the
   **Logs** tab until you see `Application startup complete`.
6. Open the URL Render shows at the top of the service, e.g. `https://resolve-desk.onrender.com`.
7. If your URL differs from what you typed for `APP_URL`, go to **Environment**, update `APP_URL` to the real URL
   and click **Save changes** (Render redeploys automatically). This only affects links inside emails.
8. **Check it works:**
   - `https://<your-url>/health` shows `{"status":"ok"}`;
   - `https://<your-url>/ready` shows `"database":true,"vector_store":true,"kv":true`;
   - the home page loads, and you can sign in with `customer@resolvedesk.dev` or `admin@resolvedesk.dev`
     (password = `DEMO_PASSWORD` in your `.env`).

### Things to know about the free plan
- **It sleeps after 15 minutes without traffic.** The first visit after that takes about 50 seconds to wake up.
  Before a demo or interview, open the site a minute early.
- Every push to the deployed branch redeploys automatically.
- Logs are under **Logs** in the Render dashboard.

---

## Option B: Manual "Web Service" (no Blueprint)

1. Render → **New +** → **Web Service** → pick the repo and branch.
2. **Runtime:** Docker (detected from the `Dockerfile`). **Instance type:** Free.
3. **Health check path:** `/health`.
4. **Environment variables:** add the same keys as in the table above, plus `DEPLOY_MODE=monolith` and
   `SERVICE_TOKEN=<any long random string>`.
5. Click **Create Web Service**, then follow steps 5–8 above.

---

## Keeping it healthy

- **Qdrant free clusters pause after about 1 week without use.** The included GitHub Action
  (`.github/workflows/nightly.yml`) runs every night and keeps the cluster active, if you add your keys as
  repository secrets: GitHub repo → **Settings** → **Secrets and variables** → **Actions** →
  **New repository secret**. Add `GEMINI_API_KEY`, `GROQ_API_KEY`, `JINA_API_KEY`, `QDRANT_URL`,
  `QDRANT_API_KEY`, `DATABASE_URL`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` (and optionally the two
  Langfuse keys). If the cluster does pause, resume it in the Qdrant Cloud console.
- **Free LLM quotas:** about 1,000 requests a day per model and 15–30 per minute. Fine for demos. The **System
  health** page shows the meters.
- **CI:** every push runs tests and the frontend build on GitHub Actions (`.github/workflows/ci.yml`).

## Roll back a deployment

The previous working code is preserved at Git tag `rollback/admin-only-base-20261004` (commit `00ec8a0`).
This update changes application code and documentation only; it does not migrate or delete database records or
change the hosted vector collections. The previous code can therefore use the same hosted data.

If a deployment fails, open the Render service's **Events** page and use **Rollback** on the last successful
deployment. If that deployment is no longer listed, use **Manual Deploy → Deploy a specific commit** and enter
`00ec8a0`. Check `/health`, `/ready`, customer sign-in, and admin sign-in after the rollback. To keep the
deployed branch on the old code, revert this change's commit on `main` and push the revert.

---

## Optional extras

| Want | Do this |
|---|---|
| **Real emails** (instead of preview-only) | Add `EMAIL_TRANSPORT=smtp`, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<gmail>`, `SMTP_PASSWORD=<Gmail app password>`, `EMAIL_FROM=Resolve Desk <your@gmail.com>`; or `EMAIL_TRANSPORT=resend` + `RESEND_API_KEY` |
| **Custom domain** | Render → service → **Settings** → **Custom Domains**, then set `APP_URL` to it |
| **Fresh database** | Point `DATABASE_URL` / `QDRANT_URL` at new instances, then run locally once: `.venv\Scripts\python -m telecom_assistant.cli seed --demo --demo-tickets 6` (it writes to whatever `.env` points at) |
| **No sleeping** | Upgrade the Render instance to Starter (paid) |

---

## Other hosts (same Docker image)

- **Railway:** New Project → Deploy from GitHub repo → it detects the `Dockerfile`. Add the same environment
  variables, then **Settings** → **Networking** → **Generate Domain**.
- **Google Cloud Run** (needs a billing account; has a free tier):
  `gcloud run deploy resolve-desk --source . --region asia-south1 --allow-unauthenticated`, then set the
  environment variables in the console.
- **Fly.io:** `fly launch` (uses the `Dockerfile`), then `fly secrets set GEMINI_API_KEY=... ...` and `fly deploy`.

The container listens on the `PORT` environment variable (default 8000) and needs no disk, because all data
lives in the hosted services.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Build fails at `npm ci` | Make sure `frontend/package-lock.json` is committed (it is) |
| `/ready` shows `"database": false` | Re-check `DATABASE_URL` (copy the whole string, including `?sslmode=require...`) |
| `"vector_store": false` | The Qdrant cluster is paused; resume it in the Qdrant console |
| Pages load but analysis says the LLM is unavailable | Check `GEMINI_API_KEY` / `GROQ_API_KEY`; see **System health** for quota meters |
| Can't sign in | Use the `DEMO_PASSWORD` from your `.env`, or register a new customer |
| Slow first load | The free instance was asleep; wait about 50 s |
