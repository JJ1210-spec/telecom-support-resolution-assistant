# Phase 2 Knowledge Service

## Completed

Implemented a FastAPI knowledge service backed by one SQLite database. It stores versioned ticket and KB payloads with local Ollama embeddings, exposes semantic search and taxonomy endpoints, and returns only resolved tickets and published articles in the `evidence` pool. The separate `unresolved` pool is queryable for future analysis. A seed command loads the synthetic corpus and replays versioned updates.

## Why

One source of truth and status-based search pools make the unresolved-to-resolved transition atomic at the record level. This avoids copying a ticket between two databases and accidentally surfacing contradictory states. Version checks reject stale events and same-version conflicting changes. Embedding model IDs are stored per row so a model change cannot silently mix incompatible vectors in search.

## Verification

Focused tests exercise unresolved exclusion, transition into evidence, stale-version rejection, KB deprecation and the HTTP search endpoint. The live seed loaded 192 resolved tickets, 96 unresolved tickets and 16 published KB articles. The update fixture then moved three tickets to resolved status, published one new KB version and deprecated one article. Live counts became 195 resolved, 93 unresolved, 15 published KB and 1 deprecated KB. See Phase 4 for the end-to-end checks.

## Limits

Cosine search is a Python scan of the eligible rows in SQLite. It supports the small demo corpus but has no index-based vector scale. Ingestion has no retry queue or dead-letter handling. The API has no authentication. These are later hardening tasks.
