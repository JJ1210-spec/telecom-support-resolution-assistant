"""Environment-backed configuration.

Every provider, model ID, threshold and quota is configuration, so swapping a free tier for a paid
tier (or a hosted service for its local fallback) never needs a code change.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Project root (data/, frontend/dist, .env). Set APP_ROOT when the package is installed into site-packages (Docker).
ROOT = Path(os.getenv("APP_ROOT") or Path(__file__).resolve().parents[2])


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _chain(name: str, default: str) -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Storage
    database_url: str = "sqlite:///.runtime/app.sqlite3"
    vector_backend: str = "local"  # qdrant | local
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    collection_suffix: str = "v1"

    # Providers
    gemini_api_key: str = ""
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    jina_api_key: str = ""
    embed_provider: str = "jina"  # jina | hash
    embed_model: str = "jina-embeddings-v3"
    embed_dim: int = 1024
    rerank_model: str = "jina-reranker-v2-base-multilingual"
    llm_chain_triage: list[str] = field(default_factory=list)
    llm_chain_draft: list[str] = field(default_factory=list)
    llm_chain_assist: list[str] = field(default_factory=list)
    llm_chain_judge: list[str] = field(default_factory=list)
    llm_timeout_s: float = 20.0

    # Upstash
    upstash_redis_url: str = ""
    upstash_redis_token: str = ""
    qstash_token: str = ""
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""
    public_base_url: str = ""
    app_url: str = "http://localhost:5173"

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Notifications
    email_transport: str = "outbox"  # outbox | smtp | resend
    email_from: str = "Support Desk <support@example.com>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    resend_api_key: str = ""
    notify_url: str = ""  # empty = in-process notification service
    deploy_mode: str = "monolith"

    # Quotas (requests per day per provider:model; pre-emptive failover at 90%)
    quota_rpd_gemini: int = 1000
    quota_rpd_groq: int = 1000
    quota_failover_ratio: float = 0.9
    llm_rpm_gemini: int = 14
    llm_rpm_groq: int = 28
    llm_tpm_groq: int = 7500
    llm_max_queue_s: float = 6.0

    # Decision thresholds (tuned on the eval set; see reports/)
    min_retrieval_score: float = 0.30
    strong_match_score: float = 0.62
    min_draft_confidence: float = 0.55
    self_service_min_confidence: float = 0.62
    self_service_min_recurrence: int = 3
    clarify_target_posterior: float = 0.80
    clarify_max_questions: int = 3
    ood_similarity: float = 0.45
    incident_window_hours: int = 6
    incident_min_tickets: int = 3
    incident_similarity: float = 0.80

    # Runtime
    runtime_dir: Path = Path(".runtime")
    service_token: str = ""
    allowed_origins: list[str] = field(default_factory=list)
    frontend_dist: Path = ROOT / "frontend" / "dist"
    cache_ttl_s: int = 3600

    @classmethod
    def from_env(cls, env_file: Path | None = None, **overrides) -> Settings:
        load_dotenv(env_file or ROOT / ".env", override=False)
        qdrant_url = _env("QDRANT_URL")
        if qdrant_url and not qdrant_url.startswith("http"):
            qdrant_url = "https://" + qdrant_url
        if qdrant_url and qdrant_url.startswith("https://") and qdrant_url.count(":") < 2:
            qdrant_url = qdrant_url.rstrip("/") + ":6333"
        values = dict(
            database_url=_env("DATABASE_URL", cls.database_url),
            vector_backend=_env("VECTOR_BACKEND", "qdrant" if qdrant_url else "local"),
            qdrant_url=qdrant_url.rstrip("/"),
            qdrant_api_key=_env("QDRANT_API_KEY"),
            collection_suffix=_env("COLLECTION_SUFFIX", cls.collection_suffix),
            gemini_api_key=_env("GEMINI_API_KEY"),
            groq_api_key=_env("GROQ_API_KEY"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            jina_api_key=_env("JINA_API_KEY"),
            embed_provider=_env("EMBED_PROVIDER", "jina" if _env("JINA_API_KEY") else "hash"),
            embed_model=_env("EMBED_MODEL", cls.embed_model),
            embed_dim=_int("EMBED_DIM", cls.embed_dim),
            rerank_model=_env("RERANK_MODEL", cls.rerank_model),
            llm_chain_triage=_chain("LLM_CHAIN_TRIAGE", "gemini:gemini-3.5-flash-lite,gemini:gemini-3.1-flash-lite,"
                                                       "groq:openai/gpt-oss-20b,groq:openai/gpt-oss-120b"),
            llm_chain_draft=_chain("LLM_CHAIN_DRAFT", "groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,"
                                                     "gemini:gemini-3.1-flash-lite,gemini:gemini-3.5-flash-lite"),
            llm_chain_assist=_chain("LLM_CHAIN_ASSIST", "groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,"
                                                       "gemini:gemini-3.1-flash-lite"),
            llm_chain_judge=_chain("LLM_CHAIN_JUDGE", "groq:qwen/qwen3.8-27b,gemini:gemini-3.5-flash-lite"),
            llm_timeout_s=_float("LLM_TIMEOUT_S", cls.llm_timeout_s),
            upstash_redis_url=_env("UPSTASH_REDIS_REST_URL"),
            upstash_redis_token=_env("UPSTASH_REDIS_REST_TOKEN"),
            qstash_token=_env("QSTASH_TOKEN"),
            qstash_current_signing_key=_env("QSTASH_CURRENT_SIGNING_KEY"),
            qstash_next_signing_key=_env("QSTASH_NEXT_SIGNING_KEY"),
            public_base_url=_env("PUBLIC_BASE_URL").rstrip("/"),
            app_url=_env("APP_URL", _env("PUBLIC_BASE_URL") or cls.app_url).rstrip("/"),
            langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
            langfuse_host=_env("LANGFUSE_HOST", cls.langfuse_host).rstrip("/"),
            email_transport=_env("EMAIL_TRANSPORT", cls.email_transport),
            email_from=_env("EMAIL_FROM", cls.email_from),
            smtp_host=_env("SMTP_HOST"),
            smtp_port=_int("SMTP_PORT", cls.smtp_port),
            smtp_user=_env("SMTP_USER"),
            smtp_password=_env("SMTP_PASSWORD"),
            resend_api_key=_env("RESEND_API_KEY"),
            notify_url=_env("NOTIFY_URL").rstrip("/"),
            deploy_mode=_env("DEPLOY_MODE", cls.deploy_mode),
            quota_rpd_gemini=_int("QUOTA_RPD_GEMINI", cls.quota_rpd_gemini),
            quota_rpd_groq=_int("QUOTA_RPD_GROQ", cls.quota_rpd_groq),
            quota_failover_ratio=_float("QUOTA_FAILOVER_RATIO", cls.quota_failover_ratio),
            llm_rpm_gemini=_int("LLM_RPM_GEMINI", cls.llm_rpm_gemini),
            llm_rpm_groq=_int("LLM_RPM_GROQ", cls.llm_rpm_groq),
            llm_tpm_groq=_int("LLM_TPM_GROQ", cls.llm_tpm_groq),
            llm_max_queue_s=_float("LLM_MAX_QUEUE_S", cls.llm_max_queue_s),
            min_retrieval_score=_float("MIN_RETRIEVAL_SCORE", cls.min_retrieval_score),
            strong_match_score=_float("STRONG_MATCH_SCORE", cls.strong_match_score),
            min_draft_confidence=_float("MIN_DRAFT_CONFIDENCE", cls.min_draft_confidence),
            self_service_min_confidence=_float("SELF_SERVICE_MIN_CONFIDENCE", cls.self_service_min_confidence),
            self_service_min_recurrence=_int("SELF_SERVICE_MIN_RECURRENCE", cls.self_service_min_recurrence),
            clarify_target_posterior=_float("CLARIFY_TARGET_POSTERIOR", cls.clarify_target_posterior),
            clarify_max_questions=_int("CLARIFY_MAX_QUESTIONS", cls.clarify_max_questions),
            ood_similarity=_float("OOD_SIMILARITY", cls.ood_similarity),
            runtime_dir=Path(_env("RUNTIME_DIR", ".runtime")),
            service_token=_env("SERVICE_TOKEN"),
            allowed_origins=_chain("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"),
            cache_ttl_s=_int("CACHE_TTL_S", cls.cache_ttl_s),
        )
        values.update(overrides)
        return cls(**values)

    def internal_token(self) -> str:
        """Shared secret for service-to-service calls (notification service, QStash-less pushes)."""
        if self.service_token:
            return self.service_token
        path = self.runtime_dir / "service-token"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
        return path.read_text(encoding="utf-8").strip()

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        return url

    def redacted(self) -> dict:
        """Configuration safe to show in the admin UI (no secrets)."""
        return {
            "database": ("postgres (neon)" if "neon.tech" in self.sqlalchemy_url else "postgres")
            if self.sqlalchemy_url.startswith("postgresql") else "sqlite",
            "vector_backend": self.vector_backend,
            "embed_provider": self.embed_provider,
            "embed_model": self.embed_model,
            "rerank_model": self.rerank_model if self.jina_api_key else None,
            "llm_chains": {
                "triage": self.llm_chain_triage, "draft": self.llm_chain_draft,
                "assist": self.llm_chain_assist, "judge": self.llm_chain_judge,
            },
            "cache": "upstash" if self.upstash_redis_url else "memory",
            "queue": "qstash" if self.qstash_token and self.public_base_url else "in-process",
            "email_transport": self.email_transport,
            "langfuse": bool(self.langfuse_public_key),
            "deploy_mode": self.deploy_mode,
            "thresholds": {
                "min_retrieval_score": self.min_retrieval_score,
                "strong_match_score": self.strong_match_score,
                "min_draft_confidence": self.min_draft_confidence,
                "self_service_min_confidence": self.self_service_min_confidence,
                "self_service_min_recurrence": self.self_service_min_recurrence,
                "clarify_target_posterior": self.clarify_target_posterior,
                "ood_similarity": self.ood_similarity,
            },
        }
