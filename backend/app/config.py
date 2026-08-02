from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PaperPilot Lab RAG"
    host: str = "0.0.0.0"
    port: int = 8787
    # Local/tests default to SQLite. Lab/prod: set DATABASE_URL to Postgres (see Compose).
    database_url: str = f"sqlite:///{(ROOT_DIR / 'data' / 'paperpilot.db').as_posix()}"
    jwt_secret: str = "change-me-in-production-lab-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7
    # Reject startup if JWT_SECRET looks like a placeholder (prod safety).
    jwt_require_strong: bool = True

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4.1-mini"
    # Optional comma-separated allowlist. Empty keeps custom OpenAI-compatible models available.
    model_options: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # Separate embedding endpoint (e.g. Zhipu) while chat stays on DeepSeek
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    # api = remote OpenAI-compatible /embeddings; local = hashing fallback (no remote embed)
    embedding_provider: str = "api"
    embedding_retries: int = 3
    # Ask OpenAI-compatible streaming endpoints to include a final usage
    # object. Unsupported providers are retried once without this option.
    chat_stream_include_usage: bool = True
    ai_usage_tracking_enabled: bool = True
    # JSON list of effective model prices. Values such as input_per_million
    # are ordinary currency units; startup converts them to integer micro-units.
    # Historical events retain their selected price version.
    ai_model_prices_json: str = ""

    pdf_max_upload_mb: int = 32
    pdf_storage_dir: str = str(ROOT_DIR / "data" / "pdfs")

    chunk_target_chars: int = 2000
    chunk_min_chars: int = 450
    chunk_overlap_ratio: float = 0.15
    max_chunks: int = 280
    embed_batch_size: int = 64
    # Concurrent embedding HTTP batches (1 = serial)
    embed_concurrency: int = 3
    # Concurrent LLM contextual-prefix enrichments during indexing
    contextual_prefix_concurrency: int = 8
    rag_top_k: int = 6
    rag_min_similarity: float = 0.22
    rag_prompt_max_chars: int = 32_000
    rag_context_max_chars: int = 20_000
    rag_inventory_max_chars: int = 5_000
    rag_cards_max_chars: int = 5_000
    rag_history_max_chars: int = 8_000
    rag_tool_evidence_max_chars: int = 12_000
    # Hierarchical conversation memory: recent turns + running summary +
    # retrieved older episodes. Memory may resolve intent, never replace PDF
    # evidence for factual answers.
    conversation_memory_enabled: bool = True
    memory_query_rewrite_enabled: bool = True
    memory_semantic_recall_enabled: bool = True
    memory_background_compaction_enabled: bool = True
    memory_model: str = ""  # defaults to the request/default chat model
    memory_summary_trigger_messages: int = 20
    memory_summary_batch_messages: int = 8
    memory_summary_max_chars: int = 6_000
    memory_episode_max_chars: int = 4_000
    memory_rewrite_history_max_chars: int = 6_000
    memory_recall_top_k: int = 4
    memory_recall_candidate_cap: int = 100
    memory_recall_max_chars: int = 4_000
    memory_recall_min_score: float = 0.18
    # Guardrails against unbounded all-document prompts.
    compare_max_documents: int = 12
    inventory_prompt_max_documents: int = 50

    # Identical query answer cache (ms). 0 = disabled.
    response_cache_ttl_ms: int = 600_000
    # Bump whenever answer/retrieval prompts change incompatibly.
    prompt_version: str = "2026-07-31-memory-v1"

    rate_limit_window_seconds: int = 900
    rate_limit_auth_max: int = 30
    rate_limit_upload_max: int = 25
    rate_limit_query_max: int = 60
    # Stricter send-code limits (public registration)
    rate_limit_send_code_ip_max: int = 10  # per IP per window below
    rate_limit_send_code_ip_window_seconds: int = 3600
    auth_code_daily_max_per_target: int = 8  # per email/phone per UTC day

    # Per-user daily quotas (0 = unlimited)
    quota_daily_queries: int = 80
    quota_daily_uploads: int = 20

    # Comma-separated origins. Use your public site URL in production (not *).
    cors_origins: str = "*"
    # Only these direct peers may supply X-Forwarded-For / X-Real-IP.
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
    health_verbose: bool = False

    # Auth: email verification via SMTP. Phone register off until SMS is wired.
    auth_code_ttl_seconds: int = 300
    auth_code_cooldown_seconds: int = 60
    auth_expose_code: bool = False  # never return codes in API in lab/prod
    auth_allow_phone_register: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    # Indexing reliability
    index_max_attempts: int = 3
    index_stale_minutes: int = 30
    index_recover_on_startup: bool = True

    # OCR fallback for scanned PDFs (pypdf empty / sparse text)
    ocr_enabled: bool = True
    ocr_min_chars_per_page: int = 40
    ocr_max_pages: int = 80
    ocr_render_dpi: int = 150

    # L1 vision routing (multimodal describe for figure-heavy pages)
    vision_enabled: bool = False
    vision_model: str = "glm-4v-flash"
    vision_api_key: str = ""  # defaults to embedding_api_key then openai_api_key
    vision_base_url: str = ""  # defaults to embedding_base_url then openai_base_url
    vision_min_image_ratio: float = 0.35

    # Embedding governance (versioned re-embed)
    embedding_version: str = "v1"
    # Prefer current embedding_version at recall; 0 = off (search all)
    retrieve_require_current_embedding: bool = True

    # Sparse BM25 recall (in-process; lab-scale stand-in for ES)
    bm25_enabled: bool = True
    bm25_candidate_cap: int = 8000
    postgres_trigram_enabled: bool = True
    postgres_trigram_candidate_cap: int = 500

    # Vector backend abstraction: pgvector now; milvus/qdrant later
    vector_backend: str = "pgvector"

    # Durable index job queue (DB-backed async ingest)
    index_job_enabled: bool = True
    # When true, API only enqueues; run `python -m app.worker` separately.
    index_external_worker: bool = False
    index_worker_poll_seconds: float = 2.0
    index_worker_recovery_seconds: int = 60
    index_worker_heartbeat_seconds: int = 15
    index_worker_stale_seconds: int = 45
    # A claimed job renews this lease while OCR / embedding is still running.
    index_worker_lease_seconds: int = 90

    # L3 contextual retrieval + hybrid
    contextual_chunk_enabled: bool = True
    hybrid_recall_enabled: bool = True
    hybrid_vector_top_n: int = 20
    hybrid_keyword_top_n: int = 20
    hybrid_rrf_k: int = 60
    rerank_provider: str = "llm"  # api | llm | off
    rerank_top_n: int = 10

    # L4 agent tools
    agent_enabled: bool = True
    agent_max_tool_rounds: int = 4
    agent_max_pages_read: int = 6

    # Index worker isolation (M4)
    index_use_thread_pool: bool = True
    index_worker_threads: int = 1

    # Chat history
    chat_history_turns: int = 6

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user)

    def jwt_secret_is_weak(self) -> bool:
        weak = {
            "",
            "change-me",
            "change-me-in-production-lab-secret",
            "lab-dev-secret-change-me",
            "secret",
            "jwt-secret",
        }
        s = (self.jwt_secret or "").strip()
        return s.lower() in weak or len(s) < 24

    def chat_model_is_allowed(self, model: str | None) -> bool:
        requested = (model or "").strip()
        if not requested:
            return True
        allowed = {m.strip() for m in self.model_options.split(",") if m.strip()}
        return not allowed or requested in allowed


@lru_cache
def get_settings() -> Settings:
    return Settings()
