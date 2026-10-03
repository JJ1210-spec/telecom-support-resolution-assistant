from pathlib import Path

from fastapi.testclient import TestClient

from telecom_assistant.assist import create_app
from telecom_assistant.config import Settings


class FakeKnowledge:
    async def taxonomy(self) -> list[dict]:
        return [{"intent": "fiber.loss_of_signal", "category": "Technical Support", "product": "Fiber"}]

    async def search(self, query: str, kind: str, limit: int) -> list[dict]:
        if kind == "ticket":
            return [{"source_id": f"T-{i}", "kind": "ticket", "score": 0.81 - i * 0.01,
                     "text": "Red LOS; line test found optical signal loss; repair restored service.",
                     "payload": {"intent": "fiber.loss_of_signal", "product": "Fiber"}} for i in range(10, 13)]
        return [{"source_id": "KB-10", "kind": "kb", "score": 0.72,
                 "text": "Check ONT power and LOS light, then run optical signal test."}]


class FakeModel:
    async def chat_json(self, system: str, user: str, max_tokens: int = 320) -> dict:
        if "classify telecom" in system:
            return {"intent": "fiber.loss_of_signal", "product": "Fiber", "severity": "P1",
                    "sentiment": "frustrated", "evidence": "red LOS", "confidence": 0.86}
        return {"summary": "Check the optical line and escalate if LOS persists.", "steps": [
            {"text": "Confirm ONT power and the LOS indicator", "citations": ["KB-10"]},
            {"text": "Run an optical signal test", "citations": ["KB-10", "FABRICATED"]},
            {"text": "Promise a free router tomorrow", "citations": ["FABRICATED"]},
        ]}


class LowScoreKnowledge(FakeKnowledge):
    async def search(self, query: str, kind: str, limit: int) -> list[dict]:
        rows = await super().search(query, kind, limit)
        return [{**row, "score": 0.51} for row in rows]


class ConflictedDraftModel(FakeModel):
    async def chat_json(self, system: str, user: str, max_tokens: int = 320) -> dict:
        if "classify telecom" in system:
            return await super().chat_json(system, user, max_tokens)
        return {"summary": "Insufficient evidence to identify the cause.",
                "steps": [{"text": "Run an optical signal test", "citations": ["KB-10"]}]}


def service_client(tmp_path: Path, settings: Settings, model: FakeModel, knowledge: FakeKnowledge) -> TestClient:
    settings = Settings(**{**settings.__dict__, "portal_db": tmp_path / "portal.db",
                           "service_token_file": tmp_path / "service-token"})
    client = TestClient(create_app(settings, model, knowledge))
    client.headers["X-Service-Token"] = settings.service_token()
    return client


def test_resolve_validates_citations_and_drops_unknown_only_step(tmp_path: Path) -> None:
    client = service_client(tmp_path, Settings(min_retrieval_score=0.25), FakeModel(), FakeKnowledge())
    response = client.post("/v1/resolve", json={"complaint": "My fiber box has a red LOS light and no internet"})
    assert response.status_code == 200
    result = response.json()
    assert result["triage"]["category"] == "Technical Support"
    assert result["decision"] == "suggested_resolution"
    assert len(result["steps"]) == 2
    assert result["steps"][1]["citations"] == ["KB-10"]
    assert "FABRICATED" not in str(result["steps"])
    assert result["warnings"]
    assert result["trace_id"]


def test_weak_retrieval_abstains(tmp_path: Path) -> None:
    client = service_client(tmp_path, Settings(min_retrieval_score=0.95), FakeModel(), FakeKnowledge())
    result = client.post("/v1/resolve", json={"complaint": "My fiber box has a red LOS light and no internet"}).json()
    assert result["decision"] == "insufficient_evidence"
    assert result["steps"] == []


def test_complaint_length_is_bounded(tmp_path: Path) -> None:
    client = service_client(tmp_path, Settings(), FakeModel(), FakeKnowledge())
    assert client.post("/v1/resolve", json={"complaint": "x"}).status_code == 422


def test_weak_evidence_returns_sources_without_draft(tmp_path: Path) -> None:
    client = service_client(tmp_path, Settings(min_draft_score=0.60), FakeModel(), LowScoreKnowledge())
    result = client.post("/v1/resolve", json={"complaint": "Something is wrong with my service"}).json()
    assert result["decision"] == "insufficient_evidence"
    assert result["sources"]
    assert result["steps"] == []


def test_draft_cannot_include_steps_while_claiming_insufficient_evidence(tmp_path: Path) -> None:
    client = service_client(tmp_path, Settings(), ConflictedDraftModel(), FakeKnowledge())
    result = client.post("/v1/resolve", json={"complaint": "My fiber box has red LOS and no internet"}).json()
    assert result["decision"] == "insufficient_evidence"
    assert result["steps"] == []
