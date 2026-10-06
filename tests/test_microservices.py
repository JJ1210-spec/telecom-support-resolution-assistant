"""HTTP contract and behavior parity tests for the extracted AI services."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from telecom_assistant.microservices.clients import RemoteResolution
from telecom_assistant.microservices.discovery import create_app as discovery_app
from telecom_assistant.microservices.resolution import create_app as resolution_app
from telecom_assistant.microservices.triage import create_app as triage_app
from telecom_assistant.services import build_services
from telecom_assistant.tickets.desk import SupportDesk


def _stable(result: dict) -> dict:
    return {key: result[key] for key in ("triage", "sources", "draft", "decision", "models", "degraded",
                                           "warnings", "query_vector", "top_similarity", "taxonomy_version")}


def _peer(services):
    return build_services(services.settings, db=services.db, llm=services.llm,
                          embedder=services.embedder, index=services.index,
                          reranker=False, kv=services.kv, remote=False)


def test_resolution_http_matches_existing_analysis(services):
    triage = triage_app(services.settings, _peer(services))
    settings = replace(services.settings, triage_service_url="http://triage.internal",
                       internal_service_token="test-secret")
    resolution = resolution_app(settings, _peer(services), httpx.ASGITransport(app=triage))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=resolution),
                                     base_url="http://resolution.internal") as client:
            for index, complaint in enumerate((
                "My broadband drops every evening around 8 and restarting the router did not help.",
                "There is an unexpected extra charge on my mobile bill.",
                "The fiber box has a red LOS light and the internet is completely down.",
                "A strange new telecom issue with no known resolution or support guide exists.",
            )):
                trace_id = f"tr_parity_{index}"
                direct = await SupportDesk(services).run_analysis(complaint, None, None, trace_id)
                response = await client.post("/api/v1/resolve", headers={"X-Service-Token": "test-secret",
                                                                        "X-Request-ID": trace_id},
                                             json={"complaint": complaint, "trace_id": trace_id})
                assert response.status_code == 200, response.text
                assert _stable(response.json()) == _stable(direct)
                assert response.headers["X-Request-ID"] == trace_id

    asyncio.run(exercise())


def test_internal_auth_and_outage_are_explicit(services):
    settings = replace(services.settings, triage_service_url="http://missing.internal",
                       internal_service_token="test-secret")
    app = resolution_app(settings, services)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://resolution.internal") as client:
            payload = {"complaint": "Broadband drops each evening", "trace_id": "tr_failure"}
            denied = await client.post("/api/v1/resolve", json=payload)
            unavailable = await client.post("/api/v1/resolve", json=payload,
                                            headers={"X-Service-Token": "test-secret"})
            alive = await client.get("/health")
            return denied, unavailable, alive

    denied, unavailable, alive = asyncio.run(exercise())
    assert denied.status_code == 403
    assert unavailable.status_code == 503
    assert alive.status_code == 200


def test_discovery_and_drift_http_contracts(services):
    settings = replace(services.settings, internal_service_token="test-secret")
    app = discovery_app(settings, services)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://discovery.internal",
                                     headers={"X-Service-Token": "test-secret"}) as client:
            taxonomy = await client.get("/api/v1/taxonomy")
            added = await client.post("/api/v1/discovery/pool", json={
                "ticket_id": "TCK-SERVICE-TEST", "text": "A novel broadband issue", "reason": "other"})
            pool = await client.get("/api/v1/discovery/pool")
            drift = await client.post("/api/v1/drift/run", json={"days": 7, "persist": False})
            return taxonomy, added, pool, drift

    taxonomy, added, pool, drift = asyncio.run(exercise())
    assert taxonomy.status_code == 200
    assert taxonomy.json()["version"] == services.registry.version()
    assert added.status_code == 200
    assert any(row["ticket_id"] == "TCK-SERVICE-TEST" for row in pool.json()["pool"])
    assert drift.status_code == 200
    assert "metrics" in drift.json()


def test_discovery_degrades_without_llm_then_requires_admin_approval(services):
    settings = replace(services.settings, internal_service_token="test-secret")
    app = discovery_app(settings, services)
    original_llm = services.discovery.llm
    vector = [1.0] + [0.0] * 511

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://discovery.internal",
                                     headers={"X-Service-Token": "test-secret"}) as client:
            for n in range(3):
                response = await client.post("/api/v1/discovery/pool", json={
                    "ticket_id": f"TCK-NEW-{n}", "text": "New repeatable telecom issue", "reason": "other",
                    "vector": vector})
                assert response.status_code == 200
            services.discovery.llm = None
            degraded = await client.post("/api/v1/discovery/run", json={})
            assert degraded.json()["pending_naming"] == 1
            assert degraded.json()["proposals"] == []
            services.discovery.llm = original_llm
            proposed = await client.post("/api/v1/discovery/run", json={})
            assert proposed.status_code == 200, proposed.text
            proposal_id = proposed.json()["proposals"][0]["id"]
            before = services.registry.version()
            approved = await client.post(f"/api/v1/discovery/proposals/{proposal_id}/approve",
                                         json={"actor": "admin-test"})
            assert approved.status_code == 200, approved.text
            assert approved.json()["taxonomy_version"] == before + 1
            assert any(c["intent"] == "network.new_issue" for c in services.registry.classes())

    try:
        asyncio.run(exercise())
    finally:
        services.discovery.llm = original_llm


def test_gateway_client_returns_same_resolution_shape(services):
    complaint = "My broadband drops every evening around 8."
    trace_id = "tr_gateway_parity"
    direct = asyncio.run(SupportDesk(services).run_analysis(complaint, None, None, trace_id))
    triage = triage_app(services.settings, _peer(services))
    resolution_settings = replace(services.settings, triage_service_url="http://triage.internal")
    resolution = resolution_app(resolution_settings, _peer(services), httpx.ASGITransport(app=triage))
    services.resolution_client = RemoteResolution("http://resolution.internal", "",
                                                   transport=httpx.ASGITransport(app=resolution))
    stages = []
    through_gateway = asyncio.run(SupportDesk(services).run_analysis(complaint, None, None, trace_id, stages.append))
    assert _stable(through_gateway) == _stable(direct)
    assert stages == ["retrieving", "triaging", "drafting"]
