"""End-to-end: intake -> ticket -> analysis -> step checklist -> escalation -> admin loop -> reopen ->
confirmation -> KB learning, plus authorization, CSRF and email acknowledgements."""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from telecom_assistant.api.app import create_app
from telecom_assistant.api.security import Accounts
from telecom_assistant.db import corpus_tickets, email_log, kb_articles, outbox, tickets, users

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(services):
    app = create_app(services.settings, services, start_workers=False)
    with TestClient(app) as test_client:
        test_client.services = services
        yield test_client


def login(client: TestClient, email: str) -> dict:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def register(client: TestClient, email: str) -> dict:
    response = client.post("/auth/register", json={"email": email, "password": PASSWORD, "name": "Priya",
                                                   "region": "Indiranagar"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def wait_analysis(client: TestClient, ticket_id: str, headers: dict | None = None) -> dict:
    for _ in range(100):
        ticket = client.get(f"/v1/tickets/{ticket_id}").json()
        if ticket["analysis_state"] != "running":
            return ticket
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def drain_outbox(client: TestClient) -> None:
    for _ in range(5):
        client.portal.call(client.services.outbox.run_once)


def test_full_lifecycle(client, services, fake_llm):
    Accounts(services.db).create("admin@test.dev", PASSWORD, "admin", "Arjun")
    csrf = register(client, "priya@test.dev")

    catalog = client.get("/v1/catalog").json()
    assert {a["id"] for a in catalog["areas"]} >= {"internet", "mobile", "billing"}

    # adaptive intake: choose the area chip, describe, answer the questions the engine picks
    state = client.post("/v1/intake/start", headers=csrf, json={
        "complaint": "My internet keeps disconnecting every few minutes", "area": "internet"}).json()
    assert state["next_question"] and state["candidates"]
    asked = 0
    while state["next_question"] and asked < 6:
        q = state["next_question"]
        pick = -1 if q["id"] == "impact" else 0  # "it's inconvenient" - an emergency answer would make it P1
        option = [q["options"][pick]["id"]] if q["options"] else []
        state = client.post(f"/v1/intake/{state['session_id']}/answer", headers=csrf, json={
            "question_id": q["id"], "option_ids": option, "text": None if option else "Since Monday"}).json()
        asked += 1
    assert state["done"] and state["answers"]

    created = client.post("/v1/tickets", headers=csrf, json={
        "complaint": "My internet keeps disconnecting every few minutes. Call me on 9876543210",
        "session_id": state["session_id"]})
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    ticket = wait_analysis(client, ticket_id)
    assert ticket["status"] in ("self_service", "escalated")
    assert ticket["issue"]["label"]

    # the acknowledgement email went through the outbox exactly once
    drain_outbox(client)
    with services.db.read() as con:
        templates = [r.template for r in con.execute(sa.select(email_log).where(email_log.c.ticket_id == ticket_id))]
    assert templates.count("ticket_received") == 1

    # every AI customer step is grounded in a self-help section; the fabricated one was dropped
    assert ticket["steps"], "expected grounded customer steps"
    assert all(any("#h" in c for c in s["citations"]) for s in ticket["steps"])
    assert all(any(e["kind"] == "guide" and e["id"] in s["citations"] for e in s["evidence"])
               for s in ticket["steps"])
    assert not any(s["text"] == "Unsupported step" for s in ticket["steps"])

    # side chat on a step, then mark every step as not working -> escalated with history
    step = ticket["steps"][0]
    chatted = client.post(f"/v1/tickets/{ticket_id}/steps/{step['id']}/chat", headers=csrf,
                          json={"text": "Where is the reset button?"}).json()
    assert any(m["step_id"] == step["id"] and m["author_role"] == "ai" for m in chatted["messages"])
    for s in ticket["steps"]:
        ticket = client.post(f"/v1/tickets/{ticket_id}/steps/{s['id']}/feedback", headers=csrf,
                             json={"status": "did_not_work", "note": "no change"}).json()
    assert ticket["status"] == "escalated"

    # admin picks it up, asks a quick-choice question, customer answers
    admin = TestClient(client.app)
    admin_csrf = login(admin, "admin@test.dev")
    queue = admin.get("/v1/admin/queue").json()["tickets"]
    assert any(t["id"] == ticket_id for t in queue)
    full = admin.post(f"/v1/admin/tickets/{ticket_id}/claim", headers=admin_csrf).json()
    assert full["status"] == "in_progress"
    brief = admin.post(f"/v1/admin/tickets/{ticket_id}/copilot", headers=admin_csrf).json()
    assert brief["next_actions"] and all(a["citations"] for a in brief["next_actions"])
    full = admin.post(f"/v1/admin/tickets/{ticket_id}/messages", headers=admin_csrf, json={
        "body": "Is the LOS light red?", "options": ["Yes", "No"], "request_info": True}).json()
    assert full["status"] == "awaiting_customer"
    ticket = client.post(f"/v1/tickets/{ticket_id}/messages", headers=csrf,
                         json={"body": "No", "answered_option": "No"}).json()
    assert ticket["status"] == "in_progress"

    # admin proposes a fix; it doesn't work -> same ticket reopened; second fix works -> resolved
    admin.post(f"/v1/admin/tickets/{ticket_id}/propose", headers=admin_csrf,
               json={"steps": ["Change Wi-Fi channel to 6"], "message": "Please try this"})
    ticket = client.post(f"/v1/tickets/{ticket_id}/confirm", headers=csrf,
                         json={"solved": False, "note": "Still dropping"}).json()
    assert ticket["status"] == "in_progress" and ticket["reopen_count"] == 1
    admin.post(f"/v1/admin/tickets/{ticket_id}/propose", headers=admin_csrf,
               json={"steps": ["Move router to 5 GHz band"], "message": "Try this instead"})
    ticket = client.get(f"/v1/tickets/{ticket_id}").json()
    assert ticket["status"] == "solution_proposed" and ticket["steps"][0]["text"] == "Move router to 5 GHz band"
    ticket = client.post(f"/v1/tickets/{ticket_id}/confirm", headers=csrf, json={"solved": True}).json()
    assert ticket["status"] == "resolved"

    # learning: summary indexed as a new searchable case, KB draft proposed, resolved email sent
    drain_outbox(client)
    with services.db.read() as con:
        learned = con.execute(sa.select(corpus_tickets).where(corpus_tickets.c.ticket_id == f"LRN-{ticket_id}")).first()
        drafts = con.execute(sa.select(kb_articles).where(kb_articles.c.source_ticket_id == ticket_id)).all()
        templates = [r.template for r in con.execute(sa.select(email_log).where(email_log.c.ticket_id == ticket_id))]
        pending = con.execute(sa.select(outbox).where(outbox.c.status != "sent")).all()
    assert learned is not None and learned.source == "learned" and learned.indexed_at is not None
    assert drafts and drafts[0].status == "draft"
    assert {"ticket_received", "admin_message", "solution_proposed", "reopened", "resolved"} <= set(templates)
    assert not pending
    hits = client.portal.call(services.retriever.search, "Evening broadband drops fixed by channel change")
    assert any(t["id"] == f"LRN-{ticket_id}" for t in hits.tickets)

    # admin publishes the learned KB draft; it becomes retrievable
    published = admin.post(f"/v1/admin/kb/{drafts[0].kb_id}/decision", headers=admin_csrf,
                           json={"action": "publish"}).json()
    assert published["status"] == "published"

    ticket = client.post(f"/v1/tickets/{ticket_id}/feedback", headers=csrf, json={"rating": 5}).json()
    assert ticket["feedback"]["rating"] == 5
    stats = admin.get("/v1/admin/stats").json()
    assert stats["tickets"] >= 1 and stats["reopen_rate"] > 0


def test_authorization_and_csrf(client, services):
    csrf_a = register(client, "a@test.dev")
    ticket_id = client.post("/v1/tickets", headers=csrf_a, json={"complaint": "My mobile data is not working"}).json()["id"]
    wait_analysis(client, ticket_id)
    assert client.post("/v1/tickets", json={"complaint": "no csrf token here"}).status_code == 403
    other = TestClient(client.app)
    register(other, "b@test.dev")
    assert other.get(f"/v1/tickets/{ticket_id}").status_code == 404
    assert other.get("/v1/admin/queue").status_code == 403
    assert other.get("/v1/admin/health").status_code == 403
    anonymous = TestClient(client.app)
    assert anonymous.get("/v1/tickets").status_code == 401


def test_p1_outage_goes_to_human_and_never_self_service(client, services):
    csrf = register(client, "c@test.dev")
    ticket_id = client.post("/v1/tickets", headers=csrf, json={
        "complaint": "Our whole street has no internet since morning, the entire building is offline"}).json()["id"]
    ticket = wait_analysis(client, ticket_id)
    assert ticket["issue"]["severity"] == "P1"
    assert ticket["route"] == "human" and ticket["status"] == "escalated"
    assert ticket["steps"] == []  # no AI steps are shown to the customer for P1


def test_llm_outage_degrades_but_ticket_survives(client, services, fake_llm):
    fake_llm.fail = True
    csrf = register(client, "d@test.dev")
    ticket_id = client.post("/v1/tickets", headers=csrf, json={"complaint": "Internet drops every evening"}).json()["id"]
    ticket = wait_analysis(client, ticket_id)
    assert ticket["status"] == "escalated" and ticket["route"] == "human"
    admin_view = client.app.state.desk.store.full(ticket_id)
    assert "triage_llm_unavailable" in admin_view["decision"]["degraded"]


def test_only_current_roles_can_sign_in_and_admin_can_claim_unassigned_ticket(client, services):
    accounts = Accounts(services.db)
    with pytest.raises(ValueError, match="Invalid role"):
        accounts.create("invalid@test.dev", PASSWORD, "retired")
    customer = accounts.create("owner@test.dev", PASSWORD, "customer")
    inactive = accounts.create("inactive@test.dev", PASSWORD, "admin")
    accounts.create("admin@test.dev", PASSWORD, "admin")
    customer_csrf = login(client, customer["email"])
    created = client.post("/v1/tickets", headers=customer_csrf,
                          json={"complaint": "My mobile data is not working"})
    assert created.status_code == 201
    ticket_id = created.json()["id"]
    wait_analysis(client, ticket_id)
    with services.db.tx() as con:
        con.execute(users.update().where(users.c.id == inactive["id"]).values(role="retired"))
        con.execute(tickets.update().where(tickets.c.id == ticket_id).values(assignee_id=inactive["id"]))
    assert client.post("/auth/login", json={"email": inactive["email"], "password": PASSWORD}).status_code == 401
    assert accounts.session(accounts.new_session(inactive["id"])[0]) is None
    admin = TestClient(client.app)
    admin_csrf = login(admin, "admin@test.dev")
    assert all(user["role"] in ("customer", "admin") for user in admin.get("/v1/admin/users").json()["users"])
    assert admin.get(f"/v1/admin/tickets/{ticket_id}").json()["assignee"] is None
    claimed = admin.post(f"/v1/admin/tickets/{ticket_id}/claim", headers=admin_csrf)
    assert claimed.status_code == 200
    assert claimed.json()["assignee"]["email"] == "admin@test.dev"
