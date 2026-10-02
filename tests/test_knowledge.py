from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from telecom_assistant.config import Settings
from telecom_assistant.knowledge import KnowledgeStore, create_app


def ticket(record_id: str, status: str, version: int) -> dict:
    resolved = status == "resolved"
    return {
        "ticket_id": record_id, "record_version": version, "status": status,
        "body": "My fiber line has a red LOS indicator", "product": "Fiber",
        "resolution_steps": ["Check optical signal", "Repair the line"] if resolved else [],
        "closure_evidence": "Customer confirmed restored service" if resolved else None,
    }


def test_status_transition_keeps_unresolved_out_of_evidence(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db", "fake-embed")
    assert store.upsert("ticket", ticket("T-1", "unresolved", 1), [1.0, 0.0]) == "inserted"
    assert store.search([1.0, 0.0], "evidence", "ticket", 5) == []
    assert [s["source_id"] for s in store.search([1.0, 0.0], "unresolved", "ticket", 5)] == ["T-1"]
    assert store.upsert("ticket", ticket("T-1", "resolved", 2), [1.0, 0.0]) == "updated"
    assert [s["source_id"] for s in store.search([1.0, 0.0], "evidence", "ticket", 5)] == ["T-1"]
    assert store.search([1.0, 0.0], "unresolved", "ticket", 5) == []
    with pytest.raises(ValueError, match="Stale version"):
        store.upsert("ticket", ticket("T-1", "unresolved", 1), [1.0, 0.0])
    assert store.upsert("ticket", ticket("T-1", "resolved", 2), [1.0, 0.0]) == "unchanged"


def test_deprecated_kb_disappears_and_equal_version_conflict_fails(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db", "fake-embed")
    article = {"kb_id": "KB-1", "article_version": 1, "status": "published", "title": "Fiber checks", "checks": ["Check LOS"]}
    store.upsert("kb", article, [1.0, 0.0])
    assert len(store.search([1.0, 0.0], "evidence", "kb", 5)) == 1
    with pytest.raises(ValueError, match="Conflicting"):
        store.upsert("kb", {**article, "status": "deprecated"}, [1.0, 0.0])
    store.upsert("kb", {**article, "article_version": 2, "status": "deprecated"}, [1.0, 0.0])
    assert store.search([1.0, 0.0], "evidence", "kb", 5) == []


def test_resolved_record_requires_closure_evidence(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db", "fake-embed")
    with pytest.raises(ValueError, match="require"):
        store.upsert("ticket", {**ticket("T-2", "resolved", 1), "closure_evidence": None}, [1.0, 0.0])


class FakeEmbedder:
    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def test_knowledge_service_searches_only_eligible_pool(tmp_path: Path) -> None:
    settings = Settings(knowledge_db=tmp_path / "api.db", embed_model="fake-embed")
    client = TestClient(create_app(settings, FakeEmbedder()))
    client.post("/v1/records", json={"kind": "ticket", "payload": ticket("T-3", "unresolved", 1)}).raise_for_status()
    evidence = client.post("/v1/search", json={"query": "red LOS", "pool": "evidence", "kind": "ticket"})
    assert evidence.json()["results"] == []
    unresolved = client.post("/v1/search", json={"query": "red LOS", "pool": "unresolved", "kind": "ticket"})
    assert unresolved.json()["results"][0]["source_id"] == "T-3"

