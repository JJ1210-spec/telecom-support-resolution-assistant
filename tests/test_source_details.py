"""Admin citation links resolve to complete stored records, with role isolation."""

from fastapi.testclient import TestClient

from telecom_assistant.api.app import create_app
from telecom_assistant.api.security import Accounts


def test_admin_source_details_are_complete_and_private(services):
    Accounts(services.db).create("source-admin@test.dev", "correct-horse-battery", "admin")
    app = create_app(services.settings, services, start_workers=False)
    with TestClient(app) as client:
        params = {"source_id": "KB-BB-DROP#c1"}
        assert client.get("/v1/admin/sources", params=params).status_code == 401
        registered = client.post("/auth/register", json={"email": "source-customer@test.dev",
            "password": "correct-horse-battery"})
        assert registered.status_code == 200
        assert client.get("/v1/admin/sources", params=params).status_code == 403

        assert client.post("/auth/login", json={"email": "source-admin@test.dev",
            "password": "correct-horse-battery"}).status_code == 200
        article = client.get("/v1/admin/sources", params=params)
        assert article.status_code == 200
        assert article.json()["section"] == "admin check 1"
        assert article.json()["article"]["checks"]
        assert article.json()["article"]["self_help"]

        case = client.get("/v1/admin/sources", params={"source_id": "T-MOB-SMS-OUT-011"})
        assert case.status_code == 200
        assert case.json()["case"]["body"]
        assert case.json()["case"]["resolution_steps"]
        assert case.json()["case"]["closure_evidence"]
        assert client.get("/v1/admin/sources", params={"source_id": "KB-BB-DROP#missing"}).status_code == 404
