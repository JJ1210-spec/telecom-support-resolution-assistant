"""Environment-backed configuration shared by the local services."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    ollama_url: str = "http://127.0.0.1:11434"
    embed_model: str = "embeddinggemma"
    chat_model: str = "qwen3:1.7b"
    knowledge_url: str = "http://127.0.0.1:8001"
    knowledge_db: Path = Path(".runtime/knowledge.sqlite3")
    min_retrieval_score: float = 0.25
    min_draft_score: float = 0.60

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            ollama_url=os.getenv("OLLAMA_URL", cls.ollama_url).rstrip("/"),
            embed_model=os.getenv("EMBED_MODEL", cls.embed_model),
            chat_model=os.getenv("CHAT_MODEL", cls.chat_model),
            knowledge_url=os.getenv("KNOWLEDGE_URL", cls.knowledge_url).rstrip("/"),
            knowledge_db=Path(os.getenv("KNOWLEDGE_DB", str(cls.knowledge_db))),
            min_retrieval_score=float(os.getenv("MIN_RETRIEVAL_SCORE", cls.min_retrieval_score)),
            min_draft_score=float(os.getenv("MIN_DRAFT_SCORE", cls.min_draft_score)),
        )
