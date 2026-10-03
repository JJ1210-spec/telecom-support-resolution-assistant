from pathlib import Path

from fastapi.testclient import TestClient

from telecom_assistant.assist import create_app
from telecom_assistant.config import Settings
from telecom_assistant.ollama import OllamaError
from telecom_assistant.portal import PortalStore, customer_steps, redact_for_model


class FakeKnowledge:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def taxonomy(self) -> list[dict]:
        return [{"intent": "wifi.coverage_or_interference", "category": "Technical Support",
                 "product": "Router/CPE"}]

    async def search(self, query: str, kind: str, limit: int) -> list[dict]:
        self.queries.append(query)
        if kind == "kb":
            return [{"source_id": "KB-WIFI", "kind": "kb", "score": 0.82,
                     "text": "Wi-Fi coverage\nRouter/CPE\nInvestigate weak signal.\n"
                             "Check router power and move the device closer to the router.\n"
                             "Escalate if service remains unavailable."}]
        return [{"source_id": "T-WIFI-1", "kind": "ticket", "score": 0.77,
                 "text": "Weak Wi-Fi in another room; moving the router helped.",
                 "payload": {"intent": "wifi.coverage_or_interference"}}]


class FakeModel:
    def __init__(self, severity: str = "P3") -> None:
        self.severity = severity

    async def chat_json(self, system: str, user: str, max_tokens: int = 320) -> dict:
        if "classify telecom" in system:
            return {"intent": "wifi.coverage_or_interference", "product": "Router/CPE",
                    "severity": self.severity, "sentiment": "concerned", "evidence": "weak Wi-Fi", "confidence": 0.84}
        return {"summary": "Check router placement and power.", "steps": [
            {"text": "Check router power and move closer to the router.", "citations": ["KB-WIFI"]},
            {"text": "Promise a free replacement tomorrow.", "citations": ["KB-WIFI"]},
        ]}


class FailedModel(FakeModel):
    async def chat_json(self, system: str, user: str, max_tokens: int = 320) -> dict:
        raise RuntimeError("model offline")


class OllamaFailedModel(FakeModel):
    async def chat_json(self, system: str, user: str, max_tokens: int = 320) -> dict:
        raise OllamaError("model offline")


def setup(tmp_path: Path, model: FakeModel | None = None) -> tuple[TestClient, PortalStore, FakeKnowledge]:
    settings = Settings(portal_db=tmp_path / "portal.db", service_token_file=tmp_path / "service-token")
    knowledge = FakeKnowledge()
    app = create_app(settings, model or FakeModel(), knowledge)
    return TestClient(app), app.state.portal_store, knowledge


def account(client: TestClient, email: str) -> str:
    response = client.post("/auth/register", json={"email": email, "password": "good-password-123"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_permissions_ownership_and_admin_details(tmp_path: Path) -> None:
    client, store, knowledge = setup(tmp_path)
    assert client.get("/admin").status_code == 401
    assert client.get("/v1/admin/tickets").status_code == 401
    assert client.post("/v1/resolve", json={"complaint": "Weak Wi-Fi at home"}).status_code == 401

    csrf_a = account(client, "alice@example.test")
    assert client.get("/customer").status_code == 200
    assert client.get("/admin").status_code == 403
    assert client.get("/v1/admin/tickets").status_code == 403
    assert client.post("/v1/tickets", json={"complaint": "Weak Wi-Fi at home"}).status_code == 403

    complaint = "Weak Wi-Fi at home; call me on 9876543210 or alice@example.test"
    created = client.post("/v1/tickets", headers={"X-CSRF-Token": csrf_a}, json={"complaint": complaint})
    assert created.status_code == 201
    ticket = created.json()
    assert ticket["complaint"] == complaint
    assert len(ticket["suggested_steps"]) == 1
    assert ticket["suggested_steps"][0]["citations"] == ["KB-WIFI"]
    assert ticket["suggested_steps"][0]["text"].startswith("Check router power")
    assert all("9876543210" not in query and "alice@example.test" not in query for query in knowledge.queries)

    client.post("/auth/logout", headers={"X-CSRF-Token": csrf_a}).raise_for_status()
    csrf_b = account(client, "bob@example.test")
    assert client.get(f"/v1/tickets/{ticket['id']}").status_code == 404
    assert client.post(f"/v1/tickets/{ticket['id']}/feedback", headers={"X-CSRF-Token": csrf_b},
                       json={"rating": "helpful"}).status_code == 404

    store.create_user("admin@example.test", "admin-password-123", "admin")
    admin_login = client.post("/auth/login", json={"email": "admin@example.test",
                                                     "password": "admin-password-123"})
    csrf_admin = admin_login.json()["csrf_token"]
    assert client.get("/admin").status_code == 200
    assert client.get("/customer").status_code == 403
    full = client.get(f"/v1/admin/tickets/{ticket['id']}").json()
    assert full["customer_email"] == "alice@example.test"
    assert full["analysis"]["sources"]
    assert full["events"]
    assert client.post(f"/v1/admin/tickets/{ticket['id']}/status", headers={"X-CSRF-Token": csrf_admin},
                       json={"status": "resolved"}).status_code == 422
    changed = client.post(f"/v1/admin/tickets/{ticket['id']}/status",
                          headers={"X-CSRF-Token": csrf_admin},
                          json={"status": "in_progress", "public_note": "We are checking the line.",
                                "admin_note": "Assigned to support."})
    assert changed.status_code == 200
    assert changed.json()["status"] == "in_progress"

    csrf_a = client.post("/auth/login", json={"email": "alice@example.test",
                                                "password": "good-password-123"}).json()["csrf_token"]
    mine = client.get(f"/v1/tickets/{ticket['id']}").json()
    assert mine["public_note"] == "We are checking the line."
    assert "admin_note" not in mine and "sources" not in mine
    feedback = client.post(f"/v1/tickets/{ticket['id']}/feedback", headers={"X-CSRF-Token": csrf_a},
                           json={"rating": "not_helpful", "comment": "Still weak"})
    assert feedback.status_code == 200
    assert store.metrics()["feedback_counts"]["not_helpful"] == 1


def test_ai_outage_keeps_ticket_and_high_severity_withholds_customer_steps(tmp_path: Path) -> None:
    client, _, _ = setup(tmp_path, FailedModel())
    csrf = account(client, "customer@example.test")
    response = client.post("/v1/tickets", headers={"X-CSRF-Token": csrf},
                           json={"complaint": "My Wi-Fi is weak in the bedroom"})
    assert response.status_code == 201
    assert response.json()["analysis_status"] == "needs_review"
    assert response.json()["suggested_steps"] == []
    assert client.get("/v1/tickets").json()["tickets"][0]["id"] == response.json()["id"]
    assert customer_steps({"decision": "suggested_resolution", "triage": {"severity": "P1"},
                           "sources": [{"source_id": "KB-WIFI", "kind": "kb", "score": 0.9,
                                        "text": "Check router power"}],
                           "steps": [{"text": "Check router power", "citations": ["KB-WIFI"]}]}) == []

    second, _, _ = setup(tmp_path / "degraded", OllamaFailedModel())
    second_csrf = account(second, "degraded@example.test")
    degraded = second.post("/v1/tickets", headers={"X-CSRF-Token": second_csrf},
                           json={"complaint": "Weak Wi-Fi in the bedroom"})
    assert degraded.status_code == 201
    assert degraded.json()["analysis_status"] == "needs_review"


def test_redaction_and_password_hashing(tmp_path: Path) -> None:
    assert "1234567890" not in redact_for_model("Phone 1234567890 and me@sample.com")
    assert "me@sample.com" not in redact_for_model("Phone 1234567890 and me@sample.com")
    store = PortalStore(tmp_path / "auth.db")
    store.create_user("person@example.test", "long-enough-password")
    with store.connect() as con:
        stored = con.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "long-enough-password" not in stored
    assert store.authenticate("person@example.test", "wrong-password") is None
    assert store.authenticate("person@example.test", "long-enough-password") is not None


def test_customer_steps_require_actual_kb_check_not_article_title() -> None:
    analysis = {"decision": "suggested_resolution", "triage": {"severity": "P3"},
                "sources": [{"source_id": "KB-WIFI", "kind": "kb", "score": 0.72,
                             "text": "Wifi Coverage Or Interference\nRouter/CPE\nGeneric summary.\n"
                                     "Check placement, walls and band selection\nEscalate if Ethernet fails."}],
                "steps": [{"text": "Check Wi-Fi coverage and interference", "citations": ["KB-WIFI"]},
                          {"text": "Move router away from obstructions and test a less congested band",
                           "citations": ["T-OLD"]}]}
    steps = customer_steps(analysis)
    assert len(steps) == 1
    assert steps[0]["text"] == "Check placement, walls and band selection"
    assert steps[0]["citations"] == ["KB-WIFI"]
