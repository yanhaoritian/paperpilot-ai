from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

settings = get_settings()
JsonStorageType = JSON().with_variant(JSONB(), "postgresql")

if settings.is_sqlite:
    EmbeddingType = JSON
else:
    from pgvector.sqlalchemy import Vector

    EmbeddingType = Vector(settings.embedding_dimensions)


class DocumentStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class IndexJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    libraries: Mapped[list["Library"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class UsageDaily(Base):
    """Per-user daily usage counters for public-beta quotas."""

    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_usage_daily_user_day"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD UTC
    query_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    upload_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ModelPriceVersion(Base):
    """Effective-dated model price snapshot used for reproducible cost reports."""

    __tablename__ = "model_price_versions"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "operation_kind",
            "version",
            name="uq_model_price_version",
        ),
        Index(
            "ix_model_price_lookup",
            "provider",
            "model",
            "operation_kind",
            "effective_from",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chat"
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    # Prices are stored as micro-currency units per one million tokens. For
    # example CNY 2.00 / 1M tokens is stored as 2_000_000.
    input_price_microunits_per_million: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    cached_input_price_microunits_per_million: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    output_price_microunits_per_million: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    per_request_microunits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AIUsageEvent(Base):
    """Append-only provider usage ledger attributed to users and research skills."""

    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_request", "request_id"),
        Index("ix_ai_usage_operation", "operation", "created_at"),
        Index("ix_ai_usage_skill", "skill_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    index_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    usage_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="provider"
    )
    price_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(12), nullable=False, default="UNPRICED"
    )
    cost_microunits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="success"
    )
    provider_request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    extra = mapped_column(JsonStorageType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthCode(Base):
    """One-time verification codes for registration (and future reset)."""

    __tablename__ = "auth_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # email | phone
    target: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="register", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship(back_populates="libraries")
    documents: Mapped[list["Document"]] = relationship(back_populates="library", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("library_id", "file_hash", name="uq_library_file_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    library_id: Mapped[str] = mapped_column(String(36), ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.pending.value, index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    index_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Per-document durable context card (title/preview/sections) for multi-doc QA
    context_snapshot = mapped_column(JsonStorageType, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    library: Mapped["Library"] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    blocks: Mapped[list["Block"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Block(Base):
    """L2 structured block with provenance."""

    __tablename__ = "blocks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    library_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="paragraph", index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bbox = mapped_column(JsonStorageType, nullable=True)
    extra = mapped_column(JsonStorageType, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="blocks")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_owner_library", "owner_id", "library_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    library_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_ids = mapped_column(JsonStorageType, nullable=True)
    extra = mapped_column(JsonStorageType, nullable=True)
    # Logical collection key: owner_id:library_id (enterprise multi-tenant mental model)
    collection_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # keyword search helper (plain text mirror for LIKE / simple scoring)
    embedding = mapped_column(EmbeddingType, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="chunks")


class IndexJob(Base):
    """Durable async ingest queue (enterprise-lite job table)."""

    __tablename__ = "index_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=IndexJobStatus.pending.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )


class WorkerHeartbeat(Base):
    """Liveness record for independently deployed background workers."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    worker_kind: Mapped[str] = mapped_column(String(32), default="index", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    extra = mapped_column(JsonStorageType, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话", nullable=False)
    library_ids = mapped_column(JsonStorageType, nullable=False, default=lambda: [])
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summarized_message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memory_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memory_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    owner: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence",
    )
    memories: Mapped[list["ConversationMemory"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMemory.created_at",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "uq_messages_conversation_sequence",
            "conversation_id",
            "sequence",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations = mapped_column(JsonStorageType, nullable=True)
    extra = mapped_column("extra", JsonStorageType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class ConversationMemory(Base):
    """A compacted, retrievable episode from older conversation turns."""

    __tablename__ = "conversation_memories"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "source_start_index",
            "source_end_index",
            name="uq_conversation_memory_range",
        ),
        Index(
            "ix_conversation_memories_owner_conversation",
            "owner_id",
            "conversation_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    owner_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        default="episode",
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_start_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_index: Mapped[int] = mapped_column(Integer, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    embedding = mapped_column(EmbeddingType, nullable=True)
    extra = mapped_column(JsonStorageType, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="memories")
