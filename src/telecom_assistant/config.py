"""Environment-backed configuration shared by the local services."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    ollama_url: str = "http://127.0.0.1:11434"
    embed_model: str = "embeddinggemma"
    chat_model: str = "qwen3:1.7b"
    knowledge_url: str = "http://127.0.0.1:8001"
    knowledge_db: Path = Path(".runtime/knowledge.sqlite3")
    portal_db: Path = Path(".runtime/portal.sqlite3")
    service_token_file: Path = Path(".runtime/service-token")
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
            portal_db=Path(os.getenv("PORTAL_DB", str(cls.portal_db))),
            service_token_file=Path(os.getenv("SERVICE_TOKEN_FILE", str(cls.service_token_file))),
            min_retrieval_score=float(os.getenv("MIN_RETRIEVAL_SCORE", cls.min_retrieval_score)),
            min_draft_score=float(os.getenv("MIN_DRAFT_SCORE", cls.min_draft_score)),
        )

    def service_token(self) -> str:
        """Create one local secret shared by the two localhost services."""
        path = self.service_token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError("Service token file is invalid")
        return token
