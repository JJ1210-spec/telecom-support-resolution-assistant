"""Vector index abstraction: Qdrant Cloud (hybrid dense + BM25 sparse) or an in-memory local index.

Both backends return per-list ranks plus the raw dense cosine so the retrieval layer can fuse with
RRF *and* gate on a calibrated similarity. Qdrant collections are versioned (`tickets_v1`) and read
through aliases (`tickets`) so an embedding-model change can be rebuilt blue/green and swapped
atomically.
"""

from __future__ import annotations

import math
import re
import time
import uuid
import zlib
from collections import Counter
from dataclasses import dataclass, field

import httpx
import numpy as np

from ..telemetry import metrics

TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)
STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "is", "it", "my", "i", "me", "for", "this", "that",
        "with", "but", "be", "are", "was", "have", "has", "not", "at", "please", "hai", "ka", "ki", "ke", "se"}
K1, B, AVG_DL = 1.2, 0.75, 60.0


def tokenize(text: str) -> list[str]:
    return [t for t in (w.casefold() for w in TOKEN.findall(text)) if t not in STOP and len(t) > 1]


def term_id(term: str) -> int:
    return zlib.crc32(term.encode()) & 0x7FFFFFFF


def bm25_doc(text: str) -> dict[int, float]:
    """Document-side BM25 term weights. IDF is applied by the index (Qdrant `modifier: idf`)."""
    tokens = tokenize(text)
    counts = Counter(tokens)
    length = max(len(tokens), 1)
    weights: dict[int, float] = {}
    for term, tf in counts.items():
        weights[term_id(term)] = tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / AVG_DL))
    return weights


def bm25_query(text: str) -> dict[int, float]:
    return {term_id(term): 1.0 for term in set(tokenize(text))}


def point_uuid(source_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"telecom:{source_id}"))


@dataclass
class Point:
    source_id: str
    dense: np.ndarray | None
    sparse: dict[int, float]
    payload: dict


@dataclass
class Hit:
    source_id: str
    payload: dict
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    sparse_rank: int | None = None
    extra: dict = field(default_factory=dict)


class VectorError(RuntimeError):
    pass


def _matches(payload: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        value = payload.get(key)
        if isinstance(expected, list | tuple | set):
            if isinstance(value, list):
                if not set(value) & set(expected):
                    return False
            elif value not in expected:
                return False
        elif isinstance(value, list):
            if expected not in value:
                return False
        elif value != expected:
            return False
    return True


class LocalIndex:
    backend = "local"

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, Point]] = {}

    async def ensure(self, name: str, dim: int) -> None:
        self.collections.setdefault(name, {})

    async def upsert(self, name: str, points: list[Point]) -> None:
        col = self.collections.setdefault(name, {})
        for point in points:
            col[point.source_id] = point

    async def set_payload(self, name: str, source_ids: list[str], payload: dict) -> None:
        for source_id in source_ids:
            if source_id in self.collections.get(name, {}):
                self.collections[name][source_id].payload.update(payload)

    async def delete(self, name: str, source_ids: list[str]) -> None:
        for source_id in source_ids:
            self.collections.get(name, {}).pop(source_id, None)

    async def count(self, name: str) -> int:
        return len(self.collections.get(name, {}))

    async def search(self, name: str, dense: np.ndarray | None, sparse: dict[int, float] | None,
                     filters: dict, limit: int) -> list[Hit]:
        points = [p for p in self.collections.get(name, {}).values() if _matches(p.payload, filters)]
        hits: dict[str, Hit] = {}
        if dense is not None:
            scored = []
            q = dense / (np.linalg.norm(dense) or 1.0)
            for p in points:
                if p.dense is None:
                    continue
                d = p.dense / (np.linalg.norm(p.dense) or 1.0)
                scored.append((float(np.dot(q, d)), p))
            scored.sort(key=lambda item: -item[0])
            for rank, (score, p) in enumerate(scored[:limit], 1):
                hits[p.source_id] = Hit(p.source_id, p.payload, dense_score=score, dense_rank=rank)
        if sparse:
            n = max(len(points), 1)
            df: Counter[int] = Counter()
            for p in points:
                df.update(p.sparse.keys())
            scored = []
            for p in points:
                score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * p.sparse[t]
                            for t in sparse if t in p.sparse)
                if score > 0:
                    scored.append((score, p))
            scored.sort(key=lambda item: -item[0])
            for rank, (score, p) in enumerate(scored[:limit], 1):
                hit = hits.setdefault(p.source_id, Hit(p.source_id, p.payload))
                hit.sparse_score, hit.sparse_rank = score, rank
        return list(hits.values())

    async def ping(self) -> bool:
        return True


class QdrantIndex:
    backend = "qdrant"

    def __init__(self, url: str, api_key: str, suffix: str = "v1") -> None:
        self.url = url.rstrip("/")
        self.suffix = suffix
        self.client = httpx.AsyncClient(timeout=15, headers={"api-key": api_key})
        self._ready: set[str] = set()
        self.override: dict[str, str] = {}  # alias -> physical collection, used during blue/green rebuilds

    def _name(self, name: str) -> str:
        return self.override.get(name, name)

    async def _req(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self.client.request(method, f"{self.url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            metrics.inc("vector_errors", backend="qdrant")
            raise VectorError(f"Qdrant unreachable: {exc}") from exc
        if response.status_code >= 400:
            metrics.inc("vector_errors", backend="qdrant")
            raise VectorError(f"Qdrant {method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    async def ensure(self, name: str, dim: int) -> None:
        if name in self._ready and name not in self.override:
            return
        physical = self.override.get(name) or f"{name}_{self.suffix}"
        existing = {c["name"] for c in (await self._req("GET", "/collections"))["result"]["collections"]}
        if physical not in existing:
            await self._req("PUT", f"/collections/{physical}", json={
                "vectors": {"dense": {"size": dim, "distance": "Cosine", "on_disk": False}},
                "sparse_vectors": {"bm25": {"modifier": "idf"}},
                "quantization_config": {"scalar": {"type": "int8", "always_ram": True}},
                "optimizers_config": {"default_segment_number": 2},
            })
            for field_name in ("status", "kind", "product", "intent", "language", "origin"):
                await self._req("PUT", f"/collections/{physical}/index",
                                json={"field_name": field_name, "field_schema": "keyword"})
        aliases = (await self._req("GET", "/aliases"))["result"]["aliases"]
        if not any(a["alias_name"] == name for a in aliases):
            await self._req("POST", "/collections/aliases", json={"actions": [
                {"create_alias": {"collection_name": physical, "alias_name": name}}]})
        self._ready.add(name)

    async def swap_alias(self, alias: str, collection: str) -> None:
        """Blue/green switch: point `alias` at a freshly built collection atomically."""
        await self._req("POST", "/collections/aliases", json={"actions": [
            {"delete_alias": {"alias_name": alias}},
            {"create_alias": {"collection_name": collection, "alias_name": alias}},
        ]})

    async def upsert(self, name: str, points: list[Point]) -> None:
        for start in range(0, len(points), 64):
            batch = []
            for p in points[start:start + 64]:
                vector: dict = {"bm25": {"indices": list(p.sparse.keys()), "values": list(p.sparse.values())}}
                if p.dense is not None:
                    vector["dense"] = [float(x) for x in p.dense]
                batch.append({"id": point_uuid(p.source_id), "vector": vector,
                              "payload": {**p.payload, "source_id": p.source_id}})
            await self._req("PUT", f"/collections/{self._name(name)}/points", params={"wait": "true"}, json={"points": batch})

    async def set_payload(self, name: str, source_ids: list[str], payload: dict) -> None:
        await self._req("POST", f"/collections/{self._name(name)}/points/payload", params={"wait": "true"},
                        json={"payload": payload, "points": [point_uuid(s) for s in source_ids]})

    async def delete(self, name: str, source_ids: list[str]) -> None:
        await self._req("POST", f"/collections/{self._name(name)}/points/delete", params={"wait": "true"},
                        json={"points": [point_uuid(s) for s in source_ids]})

    async def count(self, name: str) -> int:
        return int((await self._req("POST", f"/collections/{self._name(name)}/points/count", json={"exact": True}))
                   ["result"]["count"])

    @staticmethod
    def _filter(filters: dict) -> dict | None:
        must = []
        for key, value in filters.items():
            if isinstance(value, list | tuple | set):
                must.append({"key": key, "match": {"any": list(value)}})
            else:
                must.append({"key": key, "match": {"value": value}})
        return {"must": must} if must else None

    async def search(self, name: str, dense: np.ndarray | None, sparse: dict[int, float] | None,
                     filters: dict, limit: int) -> list[Hit]:
        flt = self._filter(filters)
        searches, kinds = [], []
        if dense is not None:
            searches.append({"query": [float(x) for x in dense], "using": "dense", "filter": flt, "limit": limit,
                             "with_payload": True})
            kinds.append("dense")
        if sparse:
            searches.append({"query": {"indices": list(sparse.keys()), "values": list(sparse.values())},
                             "using": "bm25", "filter": flt, "limit": limit, "with_payload": True})
            kinds.append("sparse")
        if not searches:
            return []
        started = time.perf_counter()
        result = (await self._req("POST", f"/collections/{self._name(name)}/points/query/batch",
                                  json={"searches": searches}))["result"]
        metrics.observe("vector_latency", (time.perf_counter() - started) * 1000, backend="qdrant")
        hits: dict[str, Hit] = {}
        for kind, batch in zip(kinds, result, strict=True):
            for rank, point in enumerate(batch["points"], 1):
                payload = point.get("payload") or {}
                source_id = payload.get("source_id", str(point["id"]))
                hit = hits.setdefault(source_id, Hit(source_id, payload))
                if kind == "dense":
                    hit.dense_score, hit.dense_rank = float(point["score"]), rank
                else:
                    hit.sparse_score, hit.sparse_rank = float(point["score"]), rank
        return list(hits.values())

    async def ping(self) -> bool:
        try:
            await self._req("GET", "/collections")
            return True
        except VectorError:
            return False
