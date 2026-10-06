"""Small HTTP clients. No service imports another service's application objects."""

from __future__ import annotations

import json

import httpx


class ServiceUnavailable(RuntimeError):
    pass


class ServiceClient:
    def __init__(self, url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url, self.transport = url.rstrip("/"), transport
        self.headers = {"X-Service-Token": token} if token else {}

    async def post(self, path: str, payload: dict, request_id: str | None = None) -> dict:
        try:
            headers = {**self.headers, **({"X-Request-ID": request_id} if request_id else {})}
            timeout = httpx.Timeout(connect=5, read=120, write=15, pool=5)
            async with httpx.AsyncClient(base_url=self.url, headers=headers, timeout=timeout,
                                         transport=self.transport) as client:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailable(f"Internal service request failed: {path}") from exc


class SyncServiceClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {"X-Service-Token": token} if token else {}

    def request(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        try:
            with httpx.Client(base_url=self.url, headers=self.headers, timeout=35) as client:
                response = client.request(method, path, json=payload, params=params)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailable(f"Internal service request failed: {path}") from exc


class RemoteTriager(ServiceClient):
    async def run(self, complaint: str, classes: list[dict], knn: dict[str, float], intake: dict | None,
                  product_hint: str | None, trace_id: str, taxonomy_version: int,
                  neighbours: list[dict] | None = None) -> tuple[dict, dict]:
        result = await self.post("/api/v1/triage", {"complaint": complaint, "classes": classes, "knn": knn,
                                                    "intake": intake, "product_hint": product_hint,
                                                    "trace_id": trace_id, "taxonomy_version": taxonomy_version,
                                                    "neighbours": neighbours or []}, request_id=trace_id)
        return result["triage"], result["meta"]


class RemoteClarify(ServiceClient):
    async def start(self, complaint: str, area: str | None = None, chosen_intent: str | None = None) -> dict:
        return await self.post("/api/v1/intake/start", {"complaint": complaint, "area": area,
                                                       "chosen_intent": chosen_intent})

    async def answer(self, state: dict, question: dict, option_ids: list[str] | None = None,
                     text: str | None = None) -> dict:
        return await self.post("/api/v1/intake/answer", {"state": state, "question": question,
                                                        "option_ids": option_ids or [], "text": text})


class RemoteResolution(ServiceClient):
    async def analyze(self, complaint: str, intake: dict | None, product_hint: str | None,
                      trace_id: str, progress=None) -> dict:
        payload = {"complaint": complaint, "intake": intake, "product_hint": product_hint, "trace_id": trace_id}
        if progress is None:
            return await self.post("/api/v1/resolve", payload, request_id=trace_id)
        headers = {**self.headers, "X-Request-ID": trace_id}
        timeout = httpx.Timeout(connect=5, read=120, write=15, pool=5)
        try:
            async with (
                httpx.AsyncClient(base_url=self.url, headers=headers, timeout=timeout,
                                  transport=self.transport) as client,
                client.stream("POST", "/api/v1/resolve/stream", json=payload) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event["type"] == "progress":
                        progress(event["stage"])
                    elif event["type"] == "result":
                        return event["result"]
                    elif event["type"] == "error":
                        raise ServiceUnavailable(event.get("detail", "Resolution failed"))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ServiceUnavailable("Internal service request failed: /api/v1/resolve/stream") from exc
        raise ServiceUnavailable("Resolution service ended without a result")


class RemoteDiscovery(SyncServiceClient):
    def add(self, ticket_id: str, text: str, reason: str, vector: list[float] | None) -> None:
        self.request("POST", "/api/v1/discovery/pool", {"ticket_id": ticket_id, "text": text,
                                                          "reason": reason, "vector": vector})

    def pool(self) -> list[dict]:
        return self.request("GET", "/api/v1/discovery/pool")["pool"]

    def proposals(self, status: str | None = "pending") -> list[dict]:
        return self.request("GET", "/api/v1/discovery/proposals", params={"status": status})["proposals"]

    async def run(self) -> dict:
        async_client = ServiceClient(self.url, self.headers.get("X-Service-Token", ""))
        return await async_client.post("/api/v1/discovery/run", {})

    def decide(self, proposal_id: str, actor: str, decision: str, edits: dict | None = None) -> dict:
        return self.request("POST", f"/api/v1/discovery/proposals/{proposal_id}/{decision}",
                            {"actor": actor, "edits": edits})


class RemoteDrift(SyncServiceClient):
    def compute(self, days: int = 7, persist: bool = True) -> dict:
        return self.request("POST", "/api/v1/drift/run", {"days": days, "persist": persist})

    def history(self, limit: int = 30) -> list[dict]:
        return self.request("GET", "/api/v1/drift/history", params={"limit": limit})["history"]

    def flag_kb(self, kb_id: str, reason: str) -> None:
        self.request("POST", f"/api/v1/drift/flag-kb/{kb_id}", {"reason": reason})
