"""LLM gateway: provider chain with schema validation, retries, circuit breakers and quota meters.

Each role (triage, draft, assist, judge) has an ordered chain such as
``gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b``. A call walks the chain until a provider
returns JSON that validates against the role's Pydantic schema:

* invalid JSON / schema mismatch -> one retry on the same model with the validation error fed back;
* HTTP 429/5xx, timeout, open breaker, or quota meter >= 90% of the daily budget -> next provider;
* every provider fails -> ``LLMUnavailable`` so the caller can degrade (retrieval-only answer).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..telemetry import Langfuse, metrics
from .kv import MemoryKV, UpstashKV


class LLMUnavailable(RuntimeError):
    def __init__(self, message: str, attempts: list[dict]) -> None:
        super().__init__(message)
        self.attempts = attempts


class ProviderError(RuntimeError):
    def __init__(self, message: str, retryable_elsewhere: bool = True) -> None:
        super().__init__(message)
        self.retryable_elsewhere = retryable_elsewhere


class RateLimited(ProviderError):
    """HTTP 429: the provider is healthy but this model's quota window is full. Not a breaker failure."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    """Sliding 60 s window over requests and (estimated) tokens for one model.

    Free tiers enforce per-minute limits (Gemini Flash-Lite: 15 RPM; Groq gpt-oss: 8k TPM). Smoothing bursts
    on our side avoids 429 storms; if the wait would exceed `max_wait`, the caller fails over instead.
    """

    def __init__(self, rpm: int | None, tpm: int | None) -> None:
        self.rpm, self.tpm = rpm, tpm
        self.events: deque[tuple[float, int]] = deque()
        self.lock = asyncio.Lock()

    def wait_needed(self, tokens: int, now: float) -> float:
        while self.events and now - self.events[0][0] >= 60:
            self.events.popleft()
        waits = [0.0]
        if self.rpm and len(self.events) >= self.rpm:
            waits.append(60 - (now - self.events[len(self.events) - self.rpm][0]))
        if self.tpm:
            used = sum(t for _, t in self.events)
            if used + tokens > self.tpm:
                freed = 0
                for ts, t in self.events:
                    freed += t
                    if used - freed + tokens <= self.tpm:
                        waits.append(60 - (now - ts))
                        break
                else:
                    waits.append(60.0)
        return max(waits)

    async def acquire(self, tokens: int, max_wait: float) -> bool:
        async with self.lock:
            wait = self.wait_needed(tokens, time.time())
            if wait > max_wait:
                return False
            if wait > 0:
                await asyncio.sleep(wait)
            self.events.append((time.time(), tokens))
            return True


@dataclass
class LLMResult:
    data: dict
    provider: str
    model: str
    latency_ms: int
    attempts: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def fallback_path(self) -> str:
        return "primary" if len(self.attempts) <= 1 else "fallback"

    @property
    def model_id(self) -> str:
        return f"{self.provider}/{self.model}"


class CircuitBreaker:
    def __init__(self, threshold: int = 5, window_s: float = 60, cooldown_s: float = 30) -> None:
        self.threshold, self.window_s, self.cooldown_s = threshold, window_s, cooldown_s
        self.failures: deque[float] = deque()
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        return time.time() - self.opened_at >= self.cooldown_s  # half-open: let one request probe

    def record(self, ok: bool) -> None:
        now = time.time()
        if ok:
            self.failures.clear()
            self.opened_at = None
            return
        self.failures.append(now)
        while self.failures and now - self.failures[0] > self.window_s:
            self.failures.popleft()
        if len(self.failures) >= self.threshold:
            self.opened_at = now

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.allow() else "open"


def extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start: end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Model returned JSON that is not an object")
    return value


ProviderFn = Callable[..., Awaitable[tuple[str, dict]]]  # (model, system, user, max_tokens, temperature, json_schema=)


class LLMGateway:
    def __init__(self, settings: Settings, kv: MemoryKV | UpstashKV, langfuse: Langfuse | None = None,
                 providers: dict[str, ProviderFn] | None = None) -> None:
        self.settings = settings
        self.kv = kv
        self.langfuse = langfuse
        self.client = httpx.AsyncClient(timeout=settings.llm_timeout_s)
        self.breakers: dict[str, CircuitBreaker] = {}
        self.limiters: dict[str, RateLimiter] = {}
        self.cooldown_until: dict[str, float] = {}
        self.providers: dict[str, ProviderFn] = {
            "gemini": self._gemini, "groq": self._groq, "anthropic": self._anthropic,
        }
        if providers:
            self.providers.update(providers)
        self.chains = {
            "triage": settings.llm_chain_triage, "draft": settings.llm_chain_draft,
            "assist": settings.llm_chain_assist, "judge": settings.llm_chain_judge,
        }

    # ------------------------------------------------------------------ providers
    async def _gemini(self, model: str, system: str, user: str, max_tokens: int, temperature: float,
                      json_schema: dict | None = None):
        if not self.settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY not configured")
        config: dict[str, Any] = {"temperature": temperature, "responseMimeType": "application/json",
                                  "maxOutputTokens": max_tokens}
        if json_schema:
            config["responseJsonSchema"] = json_schema  # constrained decoding: the shape is enforced, not hoped for
        if "lite" in model and model.startswith("gemini-3"):
            config["thinkingConfig"] = {"thinkingLevel": "minimal"}
        elif model.startswith("gemini-3") or model.startswith("gemini-2.5-flash"):
            config["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": config}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        response = await self.client.post(url, params={"key": self.settings.gemini_api_key}, json=body)
        if response.status_code == 400 and ("thinking" in response.text.lower() or "schema" in response.text.lower()):
            config.pop("thinkingConfig", None)  # model doesn't support one of the optional features: retry plain
            config.pop("responseJsonSchema", None)
            response = await self.client.post(url, params={"key": self.settings.gemini_api_key}, json=body)
        self._raise_for(response)
        payload = response.json()
        finish = (payload.get("candidates") or [{}])[0].get("finishReason")
        if finish in ("RECITATION", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            # a content filter stopped generation: retrying the same model rarely helps, fail over at once
            raise ProviderError(f"Gemini stopped generation ({finish})")
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Gemini returned no candidate ({payload.get('promptFeedback')})") from exc
        usage = payload.get("usageMetadata", {})
        return text, {"input": usage.get("promptTokenCount", 0), "output": usage.get("candidatesTokenCount", 0)}

    async def _groq(self, model: str, system: str, user: str, max_tokens: int, temperature: float,
                    json_schema: dict | None = None):
        if not self.settings.groq_api_key:
            raise ProviderError("GROQ_API_KEY not configured")
        body: dict[str, Any] = {
            "model": model, "temperature": temperature, "max_completion_tokens": max_tokens + 600,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        response = await self.client.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self.settings.groq_api_key}"})
        self._raise_for(response)
        payload = response.json()
        usage = payload.get("usage", {})
        return payload["choices"][0]["message"]["content"] or "", {
            "input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)}

    async def _anthropic(self, model: str, system: str, user: str, max_tokens: int, temperature: float,
                         json_schema: dict | None = None):
        if not self.settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY not configured")
        response = await self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": max_tokens, "temperature": temperature,
                  "system": system + "\nRespond with a single JSON object and nothing else.",
                  "messages": [{"role": "user", "content": user}]},
        )
        self._raise_for(response)
        payload = response.json()
        text = "".join(block.get("text", "") for block in payload.get("content", []))
        usage = payload.get("usage", {})
        return text, {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0)}

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code == 429:
            retry_after = 20.0
            header = response.headers.get("retry-after")
            delay = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', response.text)
            with contextlib.suppress(ValueError):
                retry_after = float(header) if header else float(delay.group(1)) if delay else retry_after
            raise RateLimited(f"rate limited (429), retry after {retry_after:.0f}s", min(retry_after, 120.0))
        if response.status_code >= 500:
            raise ProviderError(f"provider error {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(f"request rejected {response.status_code}: {response.text[:200]}")

    # ------------------------------------------------------------------ rate limits
    def _limiter(self, entry: str) -> RateLimiter:
        if entry not in self.limiters:
            provider = entry.split(":", 1)[0]
            rpm = {"gemini": self.settings.llm_rpm_gemini, "groq": self.settings.llm_rpm_groq}.get(provider)
            tpm = {"groq": self.settings.llm_tpm_groq}.get(provider)
            self.limiters[entry] = RateLimiter(rpm, tpm)
        return self.limiters[entry]

    # ------------------------------------------------------------------ quota
    def _daily_limit(self, provider: str) -> int:
        return {"gemini": self.settings.quota_rpd_gemini, "groq": self.settings.quota_rpd_groq}.get(provider, 10**9)

    async def quota_used(self, provider: str, model: str) -> int:
        day = datetime.now(UTC).strftime("%Y%m%d")
        return int(await self.kv.get(f"quota:{provider}:{model}:{day}") or 0)

    async def _consume_quota(self, provider: str, model: str) -> bool:
        """Count the request; return False when the meter says fail over pre-emptively."""
        limit = self._daily_limit(provider)
        day = datetime.now(UTC).strftime("%Y%m%d")
        used = await self.kv.incr(f"quota:{provider}:{model}:{day}", 2 * 86400)
        return used <= limit * self.settings.quota_failover_ratio

    async def quota_report(self) -> list[dict]:
        seen: set[str] = set()
        rows = []
        for chain in self.chains.values():
            for entry in chain:
                if entry in seen:
                    continue
                seen.add(entry)
                provider, model = entry.split(":", 1)
                used = await self.quota_used(provider, model)
                limit = self._daily_limit(provider)
                rows.append({"provider": provider, "model": model, "used_today": used, "daily_limit": limit,
                             "remaining_ratio": round(max(0.0, 1 - used / limit), 3) if limit < 10**9 else None,
                             "breaker": self.breakers.get(entry, CircuitBreaker()).state,
                             "cooldown_s": max(0, round(self.cooldown_until.get(entry, 0) - time.time()))})
        return rows

    # ------------------------------------------------------------------ main entry
    async def json(self, role: str, system: str, user: str, schema: type[BaseModel] | None = None, *,
                   max_tokens: int = 800, temperature: float = 0.0, trace_id: str | None = None,
                   name: str | None = None) -> LLMResult:
        chain = self.chains.get(role) or self.chains["draft"]
        attempts: list[dict] = []
        json_schema = schema.model_json_schema() if schema is not None else None
        for entry in chain:
            provider, model = entry.split(":", 1)
            call = self.providers.get(provider)
            breaker = self.breakers.setdefault(entry, CircuitBreaker())
            if call is None:
                attempts.append({"model": entry, "error": "unknown provider"})
                continue
            if not breaker.allow():
                attempts.append({"model": entry, "error": "circuit open"})
                metrics.inc("llm_skipped", provider=provider, reason="breaker")
                continue
            if self.cooldown_until.get(entry, 0) > time.time():
                attempts.append({"model": entry, "error": "rate-limit cooldown"})
                metrics.inc("llm_skipped", provider=provider, reason="cooldown")
                continue
            estimate = (len(system) + len(user)) // 3 + max_tokens
            if not await self._limiter(entry).acquire(estimate, self.settings.llm_max_queue_s):
                attempts.append({"model": entry, "error": "local rate limit (would wait too long)"})
                metrics.inc("llm_skipped", provider=provider, reason="rate_limit")
                continue
            if not await self._consume_quota(provider, model):
                attempts.append({"model": entry, "error": "quota meter above failover ratio"})
                metrics.inc("llm_skipped", provider=provider, reason="quota")
                continue
            prompt = user
            for attempt in range(2):
                started, start_ts = time.perf_counter(), datetime.now(UTC)
                try:
                    text, usage = await call(model, system, prompt, max_tokens, temperature, json_schema=json_schema)
                    data = extract_json(text)
                    if schema is not None:
                        data = schema.model_validate(data).model_dump()
                except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                    latency = round((time.perf_counter() - started) * 1000)
                    attempts.append({"model": entry, "error": f"invalid output: {str(exc)[:160]}", "ms": latency})
                    metrics.inc("llm_calls", provider=provider, role=role, outcome="invalid_json")
                    self._log(trace_id, name or role, entry, system, prompt, str(exc)[:500], start_ts, None, "WARNING")
                    if attempt == 0:
                        prompt = (f"{user}\n\nYour previous reply was rejected: {str(exc)[:300]}\n"
                                  "Return ONLY one JSON object that matches the requested fields.")
                        continue
                    breaker.record(False)
                    break
                except RateLimited as exc:
                    self.cooldown_until[entry] = time.time() + exc.retry_after
                    attempts.append({"model": entry, "error": str(exc)})
                    metrics.inc("llm_calls", provider=provider, role=role, outcome="rate_limited")
                    break
                except (ProviderError, httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                    latency = round((time.perf_counter() - started) * 1000)
                    attempts.append({"model": entry, "error": str(exc)[:200] or type(exc).__name__, "ms": latency})
                    metrics.inc("llm_calls", provider=provider, role=role, outcome="error")
                    breaker.record(False)
                    self._log(trace_id, name or role, entry, system, prompt, str(exc)[:300], start_ts, None, "ERROR")
                    break
                latency = round((time.perf_counter() - started) * 1000)
                breaker.record(True)
                attempts.append({"model": entry, "ok": True, "ms": latency})
                metrics.inc("llm_calls", provider=provider, role=role, outcome="ok")
                metrics.observe("llm_latency", latency, provider=provider, role=role)
                metrics.inc("llm_tokens", usage.get("input", 0) + usage.get("output", 0), provider=provider)
                if len(attempts) > 1:
                    metrics.inc("llm_fallback", role=role)
                self._log(trace_id, name or role, entry, system, prompt, data, start_ts, usage)
                return LLMResult(data=data, provider=provider, model=model, latency_ms=latency,
                                 attempts=attempts, usage=usage)
        metrics.inc("llm_unavailable", role=role)
        raise LLMUnavailable(f"All providers failed for role '{role}'", attempts)

    def _log(self, trace_id, name, entry, system, user, output, start, usage, level="DEFAULT") -> None:
        if self.langfuse is None or not self.langfuse.enabled:
            return
        self.langfuse.generation(trace_id, name, entry, {"system": system[:4000], "user": user[:8000]},
                                 output, start, datetime.now(UTC), usage, level=level)
