"""Embedding + rerank gateway (Jina AI) with a content-hash cache in the relational store.

* Embeddings are cached by sha256(task + text) and model, so re-indexing or re-running evals never
  pays for the same text twice, and a lost vector index can be rebuilt with zero API spend.
* `HashEmbedder` is a deterministic lexical embedder used for tests and fully offline runs. Query and
  index vectors must come from the same model, so it is never mixed with Jina vectors in one index.
* Rerank failures degrade to the fused (RRF) order; the caller records the degradation.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import zlib
from collections import OrderedDict

import httpx
import numpy as np
import sqlalchemy as sa

from ..db import Database, bytes_to_vec, embedding_cache, vec_to_bytes
from ..telemetry import metrics

TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


class EmbeddingError(RuntimeError):
    pass


class HashEmbedder:
    """Signed feature hashing over words, bigrams and char trigrams. Deterministic and free."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.model = f"hash-{dim}"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        words = [w.casefold() for w in TOKEN.findall(text)]
        features = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for word in words:
            padded = f"#{word}#"
            features.extend(padded[i:i + 3] for i in range(len(padded) - 2))
        for feature in features:
            h = zlib.crc32(feature.encode())
            vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    async def embed(self, texts: list[str], task: str = "passage") -> list[np.ndarray]:
        return [self._vector(text) for text in texts]


class JinaEmbedder:
    def __init__(self, api_key: str, model: str, dim: int, db: Database | None) -> None:
        self.api_key, self.model, self.dim, self.db = api_key, model, dim, db
        self.client = httpx.AsyncClient(timeout=30)
        self.query_cache: OrderedDict[str, np.ndarray] = OrderedDict()  # hot-path LRU; skips a DB round-trip

    @staticmethod
    def _key(text: str, task: str) -> str:
        return hashlib.sha256(f"{task}\x00{text}".encode()).hexdigest()

    def _cache_get(self, keys: list[str]) -> dict[str, np.ndarray]:
        if not self.db or not keys:
            return {}
        with self.db.read() as con:
            rows = con.execute(sa.select(embedding_cache.c.content_hash, embedding_cache.c.vector).where(
                embedding_cache.c.model == self.model, embedding_cache.c.content_hash.in_(keys))).all()
        return {row.content_hash: bytes_to_vec(row.vector) for row in rows}

    def _cache_put(self, items: dict[str, np.ndarray]) -> None:
        if not self.db or not items:
            return
        with self.db.tx() as con:
            existing = {row[0] for row in con.execute(sa.select(embedding_cache.c.content_hash).where(
                embedding_cache.c.model == self.model, embedding_cache.c.content_hash.in_(list(items))))}
            fresh = [{"content_hash": k, "model": self.model, "vector": vec_to_bytes(v)}
                     for k, v in items.items() if k not in existing]
            if fresh:
                con.execute(embedding_cache.insert(), fresh)

    async def embed(self, texts: list[str], task: str = "passage") -> list[np.ndarray]:
        if not texts:
            return []
        jina_task = "retrieval.query" if task == "query" else "retrieval.passage"
        keys = [self._key(text, jina_task) for text in texts]
        if task == "query":
            cached = {k: self.query_cache[k] for k in keys if k in self.query_cache}
        else:
            cached = await asyncio.to_thread(self._cache_get, list(set(keys)))
        metrics.inc("embed_cache", float(sum(k in cached for k in keys)), outcome="hit")
        missing = [(k, t) for k, t in dict(zip(keys, texts, strict=True)).items() if k not in cached]
        fresh: dict[str, np.ndarray] = {}
        for start in range(0, len(missing), 64):
            batch = missing[start:start + 64]
            started = time.perf_counter()
            try:
                response = await self.client.post(
                    "https://api.jina.ai/v1/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "task": jina_task, "dimensions": self.dim,
                          "input": [text[:8000] for _, text in batch]},
                )
                response.raise_for_status()
                data = response.json()["data"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                metrics.inc("embed_errors", provider="jina")
                raise EmbeddingError(f"Jina embedding failed: {exc}") from exc
            metrics.observe("embed_latency", (time.perf_counter() - started) * 1000, provider="jina")
            metrics.inc("embed_cache", float(len(batch)), outcome="miss")
            for (key, _), item in zip(batch, sorted(data, key=lambda d: d["index"]), strict=True):
                fresh[key] = np.asarray(item["embedding"], dtype=np.float32)
        if fresh and task == "query":
            self.query_cache.update(fresh)
            while len(self.query_cache) > 4096:
                self.query_cache.popitem(last=False)
        elif fresh:
            await asyncio.to_thread(self._cache_put, fresh)
        merged = {**cached, **fresh}
        return [merged[key] for key in keys]


class Reranker:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key, self.model = api_key, model
        self.enabled = bool(api_key)
        self.client = httpx.AsyncClient(timeout=10)

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]] | None:
        """Return (index, relevance) pairs, or None when reranking is unavailable (caller degrades)."""
        if not self.enabled or not documents:
            return None
        started = time.perf_counter()
        try:
            response = await self.client.post(
                "https://api.jina.ai/v1/rerank", headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "query": query[:2000], "documents": [d[:2000] for d in documents],
                      "top_n": min(top_n, len(documents)), "return_documents": False},
            )
            response.raise_for_status()
            results = response.json()["results"]
        except (httpx.HTTPError, KeyError, ValueError):
            metrics.inc("rerank_errors", provider="jina")
            return None
        metrics.observe("rerank_latency", (time.perf_counter() - started) * 1000, provider="jina")
        return [(int(item["index"]), float(item["relevance_score"])) for item in results]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if not na or not nb:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
