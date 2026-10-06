PY ?= .venv/Scripts/python
ifeq ($(OS),)
PY = .venv/bin/python
endif

.PHONY: install web check-keys seed demo updates api web-dev test lint eval drift discover up

install:            ## python + node dependencies
	python -m venv .venv && $(PY) -m pip install -e ".[dev]" && cd frontend && npm ci
web:                ## build the React app (served by the API at /)
	cd frontend && npm run build
check-keys:         ## ping every provider, show quota meters
	$(PY) -m telecom_assistant.cli check-keys
seed:               ## taxonomy, KB (with self-help sections), synthetic corpus -> DB + vector index
	$(PY) -m telecom_assistant.cli seed
demo:               ## seed + demo users + 6 demo tickets
	$(PY) -m telecom_assistant.cli seed --demo --demo-tickets 6
updates:            ## replay versioned update events (ticket resolutions, KB edit + deprecation)
	$(PY) -m telecom_assistant.cli updates
api:                ## run API + SPA on :8000
	$(PY) -m uvicorn telecom_assistant.main:app --host 127.0.0.1 --port 8000
web-dev:            ## Vite dev server on :5173 (proxies to :8000)
	cd frontend && npm run dev
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check src tests
eval:               ## full evaluation + health report -> reports/
	$(PY) -m telecom_assistant.cli eval --concurrency 1
drift:
	$(PY) -m telecom_assistant.cli drift
discover:
	$(PY) -m telecom_assistant.cli discover
up:                 ## docker compose: API + React app
	docker compose up --build -d
