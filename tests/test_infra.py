"""Reliability tests: LLM failover/breaker/quota, notification idempotency + QStash signatures, outbox DLQ,
idempotent indexing, KB deprecation, incident radar and solution-drift detection."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import numpy as np
import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from telecom_assistant.db import outbox, steps, utc_now
from telecom_assistant.gateways.kv import MemoryKV
from telecom_assistant.gateways.llm import CircuitBreaker, LLMGateway, LLMUnavailable, ProviderError
from telecom_assistant.knowledge.indexer import StaleVersion
from telecom_assistant.notify.outbox import MAX_ATTEMPTS, enqueue
from telecom_assistant.notify.service import verify_qstash_signature

from .conftest import test_settings


class Out(BaseModel):
    ok: bool


def gateway(tmp_path, providers, **overrides):
    settings = test_settings(tmp_path, llm_chain_triage=["a:m1", "b:m2"], **overrides)
    return LLMGateway(settings, MemoryKV(), None, providers=providers)


def test_llm_falls_back_and_retries_invalid_json(tmp_path):
    calls = []

    async def broken(model, system, user, max_tokens, temperature, **_):
        calls.append("a")
        raise ProviderError("429")

    async def flaky(model, system, user, max_tokens, temperature, **_):
        calls.append("b")
        return ("not json" if calls.count("b") == 1 else '{"ok": true}'), {}

    result = asyncio.run(gateway(tmp_path, {"a": broken, "b": flaky}).json("triage", "s", "u", Out))
    assert result.data == {"ok": True} and result.provider == "b" and result.fallback_path == "fallback"
    assert calls == ["a", "b", "b"]


def test_llm_all_fail_raises_unavailable(tmp_path):
    async def broken(*args, **_):
        raise ProviderError("down")

    with pytest.raises(LLMUnavailable) as exc:
        asyncio.run(gateway(tmp_path, {"a": broken, "b": broken}).json("triage", "s", "u", Out))
    assert len(exc.value.attempts) == 2


def test_quota_meter_fails_over_before_the_limit(tmp_path):
    used = []

    async def ok(name):
        async def call(*args, **_):
            used.append(name)
            return '{"ok": true}', {}
        return call

    async def run():
        gw = gateway(tmp_path, {"a": await ok("a"), "b": await ok("b")}, quota_rpd_gemini=10)
        gw.chains["triage"] = ["gemini:m1", "b:m2"]
        gw.providers["gemini"] = gw.providers["a"]
        for _ in range(12):
            await gw.json("triage", "s", "u", Out)

    asyncio.run(run())
    assert used.count("a") == 9 and used.count("b") == 3  # failover at 90% of a 10/day budget


def test_circuit_breaker_opens_and_half_opens():
    breaker = CircuitBreaker(threshold=3, window_s=60, cooldown_s=0.05)
    for _ in range(3):
        breaker.record(False)
    assert breaker.state == "open" and not breaker.allow()
    time.sleep(0.06)
    assert breaker.allow()
    breaker.record(True)
    assert breaker.state == "closed"


def _jwt(key: str, claims: dict) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps(claims).encode())
    signature = b64(hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def test_qstash_signature_verification():
    body = b'{"event_id":"e1"}'
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
    claims = {"iss": "Upstash", "sub": "https://x/notify/v1/notify", "exp": time.time() + 60, "nbf": time.time() - 1,
              "body": digest}
    token = _jwt("next-key", claims)
    assert verify_qstash_signature(token, body, "https://x/notify/v1/notify", ["current-key", "next-key"])
    assert not verify_qstash_signature(token, b'{"tampered":1}', "https://x/notify/v1/notify", ["next-key"])
    assert not verify_qstash_signature(token, body, "https://x/notify/v1/notify", ["wrong"])
    expired = _jwt("next-key", {**claims, "exp": time.time() - 100})
    assert not verify_qstash_signature(expired, body, "", ["next-key"])


def test_notifications_are_idempotent(services):
    event = {"event_id": "evt-1", "template": "ticket_received", "to": "x@test.dev", "ticket_id": "TCK-1",
             "context": {"name": "X"}}
    first = asyncio.run(services.notifications.handle(event))
    second = asyncio.run(services.notifications.handle(event))
    assert first["status"] == "captured" and second["duplicate"]
    assert len(services.notifications.list("TCK-1")) == 1


def test_outbox_dead_letters_then_replays(services):
    attempts = []

    async def failing(payload):
        attempts.append(payload["event_id"])
        raise RuntimeError("downstream down")

    services.outbox.register("flaky", failing)
    with services.db.tx() as con:
        event_id = enqueue(con, "flaky", {"x": 1})
    for _ in range(MAX_ATTEMPTS):
        with services.db.tx() as con:
            con.execute(outbox.update().values(next_attempt_at=utc_now()))
        asyncio.run(services.outbox.run_once())
    stats = services.outbox.stats()
    assert any(d["id"] == event_id for d in stats["dead_letters"]) and len(attempts) == MAX_ATTEMPTS

    async def healthy(payload):
        attempts.append("ok")

    services.outbox.register("flaky", healthy)
    assert services.outbox.replay(event_id) == 1
    asyncio.run(services.outbox.run_once())
    with services.db.read() as con:
        assert con.execute(sa.select(outbox.c.status).where(outbox.c.id == event_id)).scalar() == "sent"


def test_indexing_is_idempotent_and_versioned(services):
    row = {"ticket_id": "T-NEW-1", "record_version": 2, "status": "resolved", "subject": "Router reboot loop",
           "body": "Router keeps rebooting", "resolution_steps": ["Replaced adapter"], "intent": "wifi.coverage_or_interference"}
    assert asyncio.run(services.indexer.index_tickets([row])).inserted == 1
    assert asyncio.run(services.indexer.index_tickets([row])).unchanged == 1
    with pytest.raises(StaleVersion):
        asyncio.run(services.indexer.index_tickets([{**row, "record_version": 1}]))
    # unresolved tickets are stored but never become resolution evidence
    asyncio.run(services.indexer.index_tickets([{**row, "ticket_id": "T-OPEN-1", "status": "unresolved",
                                                 "resolution_steps": []}]))
    hits = asyncio.run(services.retriever.search("Router keeps rebooting", top_tickets=10))
    ids = [t["id"] for t in hits.tickets]
    assert "T-NEW-1" in ids and "T-OPEN-1" not in ids


def test_kb_deprecation_is_immediate(services):
    before = asyncio.run(services.retriever.search("TV picture pixelating set-top box", top_kb=10))
    assert any(s["kb_id"] == "KB-TV-PIXEL" for s in before.kb)
    from telecom_assistant.seed import kb_records

    article = next(a for a in kb_records() if a["kb_id"] == "KB-TV-PIXEL")
    assert asyncio.run(services.indexer.upsert_kb({**article, "version": 2, "status": "deprecated"})) == "status_changed"
    after = asyncio.run(services.retriever.search("TV picture pixelating set-top box", top_kb=10))
    assert not any(s["kb_id"] == "KB-TV-PIXEL" for s in after.kb)


def test_incident_radar_groups_similar_tickets_in_one_region(services):
    from telecom_assistant.db import tickets, vec_to_bytes

    vec = np.ones(16, dtype=np.float32)
    now = utc_now()
    with services.db.tx() as con:
        con.execute(sa.text("INSERT INTO users(id,email,name,password_hash,role,failed_attempts,locked_until,created_at)"
                            " VALUES ('u1','u1@test.dev','U','x','customer',0,0,:now)"), {"now": now})
        for i in range(2):
            con.execute(tickets.insert().values(
                id=f"TCK-{i}", owner_id="u1", complaint="no internet", complaint_redacted="no internet",
                status="escalated", intent="connectivity.area_outage", region="HSR", embedding=vec_to_bytes(vec),
                created_at=now, updated_at=now))
        con.execute(tickets.insert().values(id="TCK-9", owner_id="u1", complaint="x", complaint_redacted="x",
                                            status="analyzing", created_at=now, updated_at=now))
    hit = services.incidents.check("TCK-9", "fiber.loss_of_signal", "hsr", list(vec), "Outage")
    assert hit and hit["created"] and hit["size"] == 3
    assert services.incidents.check("TCK-9", "fiber.loss_of_signal", "Other City", list(vec), "Outage") is None


def test_solution_drift_flags_a_failing_kb_fix(services):
    with services.db.tx() as con:
        for i in range(5):
            con.execute(steps.insert().values(id=f"s{i}", ticket_id="T", origin="ai", position=i, text="Restart",
                                              citations=["KB-BB-DROP#h1"], customer_visible=True,
                                              status="did_not_work" if i < 4 else "worked", status_at=utc_now(),
                                              created_at=utc_now()))
    rows = services.drift.solution_drift(7)
    row = next(r for r in rows if r["kb_id"] == "KB-BB-DROP")
    assert row["flagged"] and row["recent_success_rate"] == 0.2
    report = services.drift.compute(7, persist=True)
    assert any(a["action_id"] == "review_kb:KB-BB-DROP" for a in report["alerts"])
    assert services.drift.history()


def test_rate_limiter_window():
    from telecom_assistant.gateways.llm import RateLimiter

    limiter = RateLimiter(rpm=2, tpm=1000)
    now = 1000.0
    limiter.events.extend([(now - 50, 100), (now - 10, 100)])
    assert 9.9 < limiter.wait_needed(10, now) <= 10.0  # RPM full: wait until the oldest call is 60 s old
    limiter.events.clear()
    limiter.events.extend([(now - 30, 900)])
    assert 29.9 < limiter.wait_needed(200, now) <= 30.0  # TPM full: wait for 900 tokens to expire
    assert asyncio.run(RateLimiter(rpm=1, tpm=None).acquire(10, 0)) is True


def test_429_sets_cooldown_without_tripping_breaker(tmp_path):
    from telecom_assistant.gateways.llm import RateLimited

    calls = []

    async def limited(*args, **_):
        calls.append("a")
        raise RateLimited("429", retry_after=30)

    async def ok(*args, **_):
        calls.append("b")
        return '{"ok": true}', {}

    gw = gateway(tmp_path, {"a": limited, "b": ok})
    for _ in range(3):
        assert asyncio.run(gw.json("triage", "s", "u", Out)).provider == "b"
    assert calls.count("a") == 1  # later calls skip the cooled-down model instead of hammering it
    assert gw.breakers["a:m1"].state == "closed"
