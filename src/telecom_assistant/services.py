"""Composition root: builds every component from Settings (and lets tests inject fakes)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
import sqlalchemy as sa

from .ai.assistants import Copilot, StepChat, Summarizer
from .ai.clarify import ClarifyEngine
from .ai.resolver import Resolver
from .ai.triage import Triager
from .config import Settings
from .db import Database, kb_articles
from .gateways.embeddings import HashEmbedder, JinaEmbedder, Reranker
from .gateways.kv import MemoryKV, UpstashKV, build_kv
from .gateways.llm import LLMGateway
from .gateways.vectors import LocalIndex, QdrantIndex
from .insights.discovery import Discovery
from .insights.drift import DriftMonitor
from .insights.incidents import IncidentRadar
from .knowledge.indexer import Indexer, kb_chunks
from .knowledge.retrieval import Retriever
from .knowledge.taxonomy import TaxonomyRegistry
from .notify.outbox import OutboxDispatcher
from .notify.service import NotificationService
from .telemetry import Langfuse


@dataclass
class Services:
    settings: Settings
    db: Database
    kv: MemoryKV | UpstashKV
    langfuse: Langfuse
    llm: LLMGateway | None
    embedder: JinaEmbedder | HashEmbedder
    reranker: Reranker | None
    index: LocalIndex | QdrantIndex
    indexer: Indexer
    retriever: Retriever
    registry: TaxonomyRegistry
    clarify: ClarifyEngine
    triager: Triager
    resolver: Resolver
    copilot: Copilot
    summarizer: Summarizer
    step_chat: StepChat
    notifications: NotificationService
    outbox: OutboxDispatcher
    incidents: IncidentRadar
    discovery: Discovery
    drift: DriftMonitor


def build_services(settings: Settings, *, db: Database | None = None, llm: LLMGateway | None | bool = True,
                   embedder=None, index=None, reranker: Reranker | None | bool = True,
                   kv: MemoryKV | UpstashKV | None = None) -> Services:
    db = db or Database(settings.sqlalchemy_url)
    db.create_all()
    kv = kv or build_kv(settings.upstash_redis_url, settings.upstash_redis_token)
    langfuse = Langfuse(settings)
    if llm is True:
        llm = LLMGateway(settings, kv, langfuse)
    elif llm is False:
        llm = None
    if embedder is None:
        if settings.embed_provider == "jina" and settings.jina_api_key:
            embedder = JinaEmbedder(settings.jina_api_key, settings.embed_model, settings.embed_dim, db)
        else:
            embedder = HashEmbedder(512)
    dim = embedder.dim if isinstance(embedder, HashEmbedder) else settings.embed_dim
    if reranker is True:
        reranker = Reranker(settings.jina_api_key, settings.rerank_model) if settings.jina_api_key else None
    elif reranker is False:
        reranker = None
    if index is None:
        if settings.vector_backend == "qdrant" and settings.qdrant_url and isinstance(embedder, JinaEmbedder):
            index = QdrantIndex(settings.qdrant_url, settings.qdrant_api_key, settings.collection_suffix)
        else:
            index = LocalIndex()
    indexer = Indexer(db, index, embedder, dim)
    retriever = Retriever(index, embedder, reranker)
    registry = TaxonomyRegistry(db)

    def _load_kb(kb_ids: list[str], intent: str | None) -> list:
        condition = kb_articles.c.kb_id.in_(kb_ids)
        if intent and intent != "other":
            condition = sa.or_(condition, kb_articles.c.intent == intent)
        with db.read() as con:
            return con.execute(sa.select(kb_articles).where(condition, kb_articles.c.status == "published")).all()

    async def expand_kb(kb_ids: list[str], intent: str | None = None, max_articles: int = 2) -> list[dict]:
        """Parent-document expansion: the triaged intent's own article first, then the best retrieved ones."""
        rows = await asyncio.to_thread(_load_kb, kb_ids, intent)
        order = {kb_id: i + 1 for i, kb_id in enumerate(kb_ids)}
        rows = sorted(rows, key=lambda r: (0 if intent and r.intent == intent else 1, order.get(r.kb_id, 99)))
        sections = []
        for row in rows[:max_articles]:
            for chunk in kb_chunks(dict(row._mapping)):
                sections.append({"id": chunk["source_id"], "kind": "kb", "title": chunk["title"],
                                 "text": chunk["text"], "snippet": chunk["text"][:400], "kb_id": chunk["kb_id"],
                                 "section": chunk["section"], "audience": chunk["audience"],
                                 "intent": chunk["intent"], "product": chunk["product"], "origin": chunk["origin"]})
        return sections

    notifications = NotificationService(settings, db)
    return Services(
        settings=settings, db=db, kv=kv, langfuse=langfuse, llm=llm, embedder=embedder, reranker=reranker,
        index=index, indexer=indexer, retriever=retriever, registry=registry,
        clarify=ClarifyEngine(settings, registry, retriever, llm), triager=Triager(llm),
        resolver=Resolver(settings, llm, expand_kb), copilot=Copilot(llm), summarizer=Summarizer(llm),
        step_chat=StepChat(llm), notifications=notifications,
        outbox=OutboxDispatcher(settings, db, notifications), incidents=IncidentRadar(settings, db),
        discovery=Discovery(db, registry, retriever, llm), drift=DriftMonitor(settings, db),
    )


def as_vector(values) -> list[float] | None:
    return None if values is None else [float(x) for x in np.asarray(values).ravel()]
