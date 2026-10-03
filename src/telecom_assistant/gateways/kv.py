"""Key-value store for response cache, quota meters and rate limits.

Upstash Redis (REST) is shared by every instance, so all replicas see one quota view. If Upstash is
not configured or unreachable the store silently degrades to process memory: a cache miss or a
per-instance quota count is always preferable to failing a customer request.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..telemetry import metrics


class MemoryKV:
    backend = "memory"

    def __init__(self) -> None:
        self.data: dict[str, tuple[Any, float | None]] = {}

    def _alive(self, key: str) -> bool:
        item = self.data.get(key)
        if not item:
            return False
        if item[1] is not None and item[1] < time.time():
            self.data.pop(key, None)
            return False
        return True

    async def get(self, key: str) -> Any:
        return self.data[key][0] if self._alive(key) else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.data[key] = (value, time.time() + ttl if ttl else None)

    async def incr(self, key: str, ttl: int) -> int:
        current = int(await self.get(key) or 0) + 1
        expiry = self.data[key][1] if self._alive(key) else time.time() + ttl
        self.data[key] = (current, expiry)
        return current

    async def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self.data if key.startswith(prefix)]
        for key in keys:
            self.data.pop(key, None)
        return len(keys)

    async def ping(self) -> bool:
        return True


class UpstashKV:
    backend = "upstash"

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.fallback = MemoryKV()
        self.client = httpx.AsyncClient(timeout=3.0)

    async def _cmd(self, *args: Any) -> Any:
        started = time.perf_counter()
        try:
            response = await self.client.post(self.url, json=[str(a) for a in args], headers=self.headers)
            response.raise_for_status()
            metrics.observe("kv_latency", (time.perf_counter() - started) * 1000, backend="upstash")
            return response.json().get("result")
        except (httpx.HTTPError, ValueError):
            metrics.inc("kv_errors", backend="upstash")
            raise

    async def _pipeline(self, commands: list[list[Any]]) -> list[Any]:
        response = await self.client.post(f"{self.url}/pipeline", headers=self.headers,
                                          json=[[str(a) for a in cmd] for cmd in commands])
        response.raise_for_status()
        return [item.get("result") for item in response.json()]

    async def get(self, key: str) -> Any:
        try:
            raw = await self._cmd("GET", key)
        except (httpx.HTTPError, ValueError):
            return await self.fallback.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        try:
            if ttl:
                await self._cmd("SET", key, payload, "EX", ttl)
            else:
                await self._cmd("SET", key, payload)
        except (httpx.HTTPError, ValueError):
            await self.fallback.set(key, value, ttl)

    async def incr(self, key: str, ttl: int) -> int:
        try:
            results = await self._pipeline([["INCR", key], ["EXPIRE", key, ttl, "NX"]])
            return int(results[0])
        except (httpx.HTTPError, ValueError, TypeError, IndexError):
            return await self.fallback.incr(key, ttl)

    async def delete_prefix(self, prefix: str) -> int:
        try:
            cursor, deleted = "0", 0
            while True:
                cursor, keys = await self._cmd("SCAN", cursor, "MATCH", f"{prefix}*", "COUNT", 200)
                if keys:
                    deleted += int(await self._cmd("DEL", *keys) or 0)
                if str(cursor) == "0":
                    return deleted
        except (httpx.HTTPError, ValueError, TypeError):
            return await self.fallback.delete_prefix(prefix)

    async def ping(self) -> bool:
        try:
            return await self._cmd("PING") == "PONG"
        except (httpx.HTTPError, ValueError):
            return False


def build_kv(url: str, token: str) -> MemoryKV | UpstashKV:
    return UpstashKV(url, token) if url and token else MemoryKV()


async def rate_limited(kv: MemoryKV | UpstashKV, bucket: str, limit: int, window_s: int) -> bool:
    """Fixed-window limiter. Returns True when the caller is over the limit."""
    window = int(time.time() // window_s)
    count = await kv.incr(f"rl:{bucket}:{window}", window_s + 5)
    return count > limit
