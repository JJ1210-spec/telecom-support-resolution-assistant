"""Hybrid retrieval: dense (Jina) + sparse (BM25) -> RRF fusion -> outcome boost -> cross-encoder rerank.

Returned sources carry three scores with different jobs:
* `similarity` — dense cosine, calibrated enough to gate on (abstain / OOD / recurrence);
* `rerank` — cross-encoder relevance, used for ordering when available;
* `rrf` — fused rank score, the ordering fallback when rerank is down.
Every degradation (no dense vectors, no rerank) is reported so it reaches the trace and the UI.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..gateways.embeddings import EmbeddingError, HashEmbedder, JinaEmbedder, Reranker
from ..gateways.vectors import LocalIndex, QdrantIndex, VectorError, bm25_query
from ..telemetry import Timer, metrics
from .indexer import KB, TICKETS

RRF_K = 60


@dataclass
class RetrievalResult:
    tickets: list[dict]
    kb: list[dict]
    degraded: list[str] = field(default_factory=list)
    latency_ms: dict = field(default_factory=dict)
    query_vector: list[float] | None = None

    @property
    def top_similarity(self) -> float:
        scores = [s["similarity"] for s in self.tickets + self.kb if s.get("similarity") is not None]
        return max(scores) if scores else 0.0

    def all_sources(self) -> list[dict]:
        return self.kb + self.tickets


def _snippet(payload: dict) -> str:
    if payload.get("kind") == "kb":
        return payload.get("text", "")[:400]
    steps = payload.get("steps") or []
    return (f"{payload.get('problem', '')[:220]} — Root cause: {payload.get('root_cause') or 'n/a'}. "
            f"Fix: {'; '.join(steps[:3])}")[:500]


def to_source(hit, kind: str) -> dict:
    payload = hit.payload
    text = payload.get("text", "")
    section = payload.get("section")
    check_number = hit.source_id.rsplit("#c", 1)[-1]
    if kind == "kb" and "#c" in hit.source_id and check_number.isdigit() and " — " in text:
        _, _, check_text = text.rpartition(" — ")
        _, separator, action = check_text.partition(": ")
        if separator:
            text = f"{payload.get('title', '')} — admin check {check_number}: {action}"
            section = f"admin check {check_number}"
    return {
        "id": hit.source_id, "kind": kind, "title": payload.get("title", ""),
        "snippet": _snippet({**payload, "text": text}),
        "text": text, "intent": payload.get("intent"), "product": payload.get("product"),
        "severity": payload.get("severity"), "root_cause": payload.get("root_cause"),
        "steps": payload.get("steps") or [], "section": section, "kb_id": payload.get("kb_id"),
        "audience": "customer" if payload.get("audience") == "customer" else "admin",
        "origin": payload.get("origin"), "outcome_score": payload.get("outcome_score"),
        "resolved_at": payload.get("resolved_at"),
        "similarity": round(hit.dense_score, 4) if hit.dense_score is not None else None,
        "rrf": 0.0, "rerank": None,
    }


class Retriever:
    def __init__(self, index: LocalIndex | QdrantIndex, embedder: JinaEmbedder | HashEmbedder,
                 reranker: Reranker | None) -> None:
        self.index, self.embedder, self.reranker = index, embedder, reranker

    async def embed_query(self, text: str) -> tuple[list[float] | None, list[str]]:
        try:
            return list((await self.embedder.embed([text], task="query"))[0]), []
        except EmbeddingError:
            return None, ["dense_unavailable"]

    async def search(self, query: str, *, filters: dict | None = None, top_tickets: int = 5, top_kb: int = 4,
                     rerank: bool = True, query_vector: list[float] | None = None,
                     mode: str = "hybrid") -> RetrievalResult:
        timer = Timer()
        degraded: list[str] = []
        if query_vector is None and mode != "sparse":
            query_vector, embed_degraded = await self.embed_query(query)
            degraded += embed_degraded
        timer.mark("embed")
        dense = np.asarray(query_vector, dtype=np.float32) if query_vector is not None and mode != "sparse" else None
        sparse = bm25_query(query) if mode != "dense" else None
        base = dict(filters or {})

        async def one(kind: str, status: str) -> list[dict]:
            try:
                hits = await self.index.search(kind, dense, sparse, {**base, "status": status}, limit=30)
            except VectorError:
                degraded.append("vector_store_unavailable")
                hits = []
            fused = []
            for hit in hits:
                source = to_source(hit, "ticket" if kind == TICKETS else "kb")
                rrf = sum(1 / (RRF_K + r) for r in (hit.dense_rank, hit.sparse_rank) if r)
                if source["kind"] == "ticket":
                    rrf *= 0.85 + 0.3 * float(source.get("outcome_score") or 0.5)  # outcome-weighted retrieval
                source["rrf"] = round(rrf, 6)
                fused.append(source)
            fused.sort(key=lambda s: -s["rrf"])
            return self._dedupe(fused)[:20]

        # tickets and KB are independent collections: query them concurrently
        tickets, kb = await asyncio.gather(one(TICKETS, "resolved"), one(KB, "published"))
        timer.mark("search")

        async def rerank_list(items: list[dict]) -> list[dict] | None:
            if not items:
                return items
            ranked = await self.reranker.rerank(query, [i["text"] for i in items], len(items))
            if ranked is None:
                return None
            for index, score in ranked:
                items[index]["rerank"] = round(score, 4)
            return sorted(items, key=lambda s: -(s["rerank"] or 0))

        if rerank and self.reranker is not None and self.reranker.enabled:
            reranked_tickets, reranked_kb = await asyncio.gather(rerank_list(tickets), rerank_list(kb))
            if reranked_tickets is None or reranked_kb is None:
                degraded.append("rerank_unavailable")
            tickets = reranked_tickets if reranked_tickets is not None else tickets
            kb = reranked_kb if reranked_kb is not None else kb
        elif rerank and (self.reranker is None or not self.reranker.enabled):
            degraded.append("rerank_disabled")
        timer.mark("rerank")
        for source in tickets + kb:
            if source["similarity"] is None:
                # Sparse-only degraded mode: map rerank (or a squashed RRF) to a conservative similarity.
                source["similarity"] = round(source["rerank"] if source["rerank"] is not None
                                             else min(0.5, source["rrf"] * 20), 4)
        result = RetrievalResult(tickets=tickets[:max(top_tickets, 10)], kb=kb[:top_kb], degraded=sorted(set(degraded)),
                                 latency_ms={**timer.marks, "total": timer.total()}, query_vector=query_vector)
        metrics.observe("retrieval_latency", timer.total())
        for item in degraded:
            metrics.inc("retrieval_degraded", reason=item)
        return result

    @staticmethod
    def _dedupe(items: list[dict]) -> list[dict]:
        seen, out = set(), []
        for item in items:
            key = (item["kind"], item["title"].casefold().strip(), item.get("root_cause"))
            if item["kind"] == "ticket" and key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out


def knn_votes(tickets: list[dict], k: int = 10, min_similarity: float = 0.0) -> dict[str, float]:
    """Similarity-weighted intent distribution over the k nearest resolved tickets."""
    weights: dict[str, float] = defaultdict(float)
    for source in tickets[:k]:
        sim = source.get("similarity") or 0.0
        if source.get("intent") and sim >= min_similarity:
            weights[source["intent"]] += math.exp(6 * sim)
    total = sum(weights.values())
    return {intent: round(w / total, 4) for intent, w in sorted(weights.items(), key=lambda kv: -kv[1])} if total else {}


def recurrence(tickets: list[dict], intent: str | None, threshold: float) -> int:
    """How many strongly similar resolved tickets share this intent — the 'seen it before' signal."""
    return sum(1 for s in tickets if s.get("intent") == intent and (s.get("similarity") or 0) >= threshold)
