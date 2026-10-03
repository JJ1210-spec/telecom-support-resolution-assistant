"""Observability: structured JSON logs, an in-process metrics registry and Langfuse LLM traces.

* `metrics` keeps counters and latency histograms in memory; `/metrics` exposes them in Prometheus text
  format (scrapeable by Prometheus / Grafana Agent) and the admin Health page reads them as JSON.
* LLM generations are batched to the Langfuse ingestion API in the background (never on the hot path).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(), "level": record.levelname, "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_telecom", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._telecom = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


log = logging.getLogger("telecom")


def log_event(msg: str, **fields: Any) -> None:
    log.info(msg, extra={"fields": fields})


_BUCKETS = (25, 50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000)


class Metrics:
    """Thread-safe counters + histograms with label support and a recent-sample window for percentiles."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self.samples: dict[tuple[str, tuple], deque[float]] = defaultdict(lambda: deque(maxlen=2000))
        self.hist: dict[tuple[str, tuple], list[int]] = defaultdict(lambda: [0] * (len(_BUCKETS) + 1))

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self.counters[key] += value

    def observe(self, name: str, value_ms: float, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self.samples[key].append(value_ms)
            buckets = self.hist[key]
            for index, bound in enumerate(_BUCKETS):
                if value_ms <= bound:
                    buckets[index] += 1
                    break
            else:
                buckets[-1] += 1

    @staticmethod
    def _pct(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
        return round(ordered[index], 1)

    def snapshot(self) -> dict:
        with self._lock:
            counters: dict[str, list[dict]] = defaultdict(list)
            for (name, labels), value in self.counters.items():
                counters[name].append({"labels": dict(labels), "value": value})
            latencies = {}
            for (name, labels), values in self.samples.items():
                data = list(values)
                label_text = ",".join(f"{k}={v}" for k, v in labels)
                latencies[f"{name}{{{label_text}}}" if label_text else name] = {
                    "count": len(data), "p50": self._pct(data, 0.5), "p95": self._pct(data, 0.95),
                    "p99": self._pct(data, 0.99),
                }
        return {"counters": dict(counters), "latency_ms": latencies}

    def prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                label_text = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"{name}_total{{{label_text}}} {value}")
            for (name, labels), buckets in sorted(self.hist.items()):
                base = ",".join(f'{k}="{v}"' for k, v in labels)
                cumulative = 0
                for bound, count in zip((*_BUCKETS, "+Inf"), buckets, strict=True):
                    cumulative += count
                    sep = "," if base else ""
                    lines.append(f'{name}_ms_bucket{{{base}{sep}le="{bound}"}} {cumulative}')
                lines.append(f"{name}_ms_count{{{base}}} {cumulative}")
        return "\n".join(lines) + "\n"


metrics = Metrics()


class Timer:
    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.marks: dict[str, int] = {}
        self._last = self.start

    def mark(self, name: str) -> int:
        now = time.perf_counter()
        elapsed = round((now - self._last) * 1000)
        self.marks[name] = self.marks.get(name, 0) + elapsed
        self._last = now
        return elapsed

    def total(self) -> int:
        return round((time.perf_counter() - self.start) * 1000)


class Langfuse:
    """Minimal async batch client for the Langfuse public ingestion API."""

    def __init__(self, settings: Settings) -> None:
        self.enabled = bool(settings.langfuse_public_key and settings.langfuse_secret_key)
        self.url = f"{settings.langfuse_host}/api/public/ingestion"
        token = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}
        self.queue: deque[dict] = deque(maxlen=5000)

    def _event(self, kind: str, body: dict) -> None:
        if self.enabled:
            self.queue.append({"id": str(uuid.uuid4()), "timestamp": datetime.now(UTC).isoformat(),
                               "type": kind, "body": body})

    def trace(self, trace_id: str, name: str, input: Any = None, output: Any = None, **metadata: Any) -> None:
        self._event("trace-create", {"id": trace_id, "name": name, "input": input, "output": output,
                                     "metadata": metadata or None})

    def generation(self, trace_id: str | None, name: str, model: str, input: Any, output: Any,
                   start: datetime, end: datetime, usage: dict | None, level: str = "DEFAULT",
                   status: str | None = None) -> None:
        body = {"id": str(uuid.uuid4()), "traceId": trace_id or str(uuid.uuid4()), "name": name, "model": model,
                "input": input, "output": output, "startTime": start.isoformat(), "endTime": end.isoformat(),
                "level": level, "statusMessage": status}
        if usage:
            body["usage"] = usage
        self._event("generation-create", body)

    async def flush(self) -> None:
        if not self.enabled or not self.queue:
            return
        batch = [self.queue.popleft() for _ in range(min(len(self.queue), 100))]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.url, json={"batch": batch}, headers=self.headers)
                metrics.inc("langfuse_flush", status=str(response.status_code))
        except httpx.HTTPError:
            metrics.inc("langfuse_flush", status="error")

    async def run(self, interval: float = 5.0) -> None:
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await self.flush()
