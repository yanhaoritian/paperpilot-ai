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
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # Separate embedding endpoint (e.g. Zhipu) while chat stays on DeepSeek
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    # api = remote OpenAI-compatible /embeddings; local = hashing fallback (no remote embed)
    embedding_provider: str = "api"
    embedding_retries: int = 3

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

    # Identical query answer cache (ms). 0 = disabled.
    response_cache_ttl_ms: int = 600_000

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

    # Vector backend abstraction: pgvector now; milvus/qdrant later
    vector_backend: str = "pgvector"

    # Durable index job queue (DB-backed async ingest)
    index_job_enabled: bool = True

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
