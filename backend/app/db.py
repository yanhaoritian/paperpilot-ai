from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(
    settings.database_url,
    pool_pre_ping=not settings.is_sqlite,
    connect_args=connect_args,
    **({} if settings.is_sqlite else {"pool_size": 5, "max_overflow": 10}),
)

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    if not settings.is_sqlite:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_users_columns()
    _migrate_documents_columns()
    _migrate_chat_columns()
    _migrate_chunk_columns()
    _ensure_pgvector_indexes()


def _migrate_chunk_columns() -> None:
    cols_needed = {
        "section_path": "VARCHAR(512)",
        "role": "VARCHAR(32)",
        "context_prefix": "TEXT",
        "block_ids": "JSON",
        "extra": "JSON",
        "collection_key": "VARCHAR(128)",
        "embedding_model": "VARCHAR(128)",
        "embedding_version": "VARCHAR(32)",
    }
    with engine.begin() as conn:
        if settings.is_sqlite:
            existing = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(chunks)")).fetchall()
            }
            for name, typ in cols_needed.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE chunks ADD COLUMN {name} {typ}"))
        else:
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section_path VARCHAR(512)"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS role VARCHAR(32)"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS context_prefix TEXT"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS block_ids JSONB"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS extra JSONB"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS collection_key VARCHAR(128)"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(32)"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_collection_key ON chunks (collection_key)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_version ON chunks (embedding_version)"
                )
            )


def _ensure_pgvector_indexes() -> None:
    """Create ANN index for embedding search (Postgres only)."""
    if settings.is_sqlite:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
                ON chunks
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_owner_library ON chunks (owner_id, library_id)"
            )
        )


def _migrate_users_columns() -> None:
    """Best-effort schema upgrades for existing SQLite/Postgres installs."""
    with engine.begin() as conn:
        if settings.is_sqlite:
            cols = {
                row[1]: row  # cid, name, type, notnull, dflt, pk
                for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()
            }
            if "phone" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(32)"))
            # Rebuild users table if email is still NOT NULL
            email_notnull = bool(cols.get("email") and cols["email"][3])
            if email_notnull or "phone" not in {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS users_new (
                          id VARCHAR(36) PRIMARY KEY,
                          username VARCHAR(64) NOT NULL UNIQUE,
                          email VARCHAR(255) UNIQUE,
                          phone VARCHAR(32) UNIQUE,
                          password_hash VARCHAR(255) NOT NULL,
                          created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                existing = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")).fetchone()
                if existing:
                    conn.execute(
                        text(
                            """
                            INSERT OR IGNORE INTO users_new (id, username, email, phone, password_hash, created_at)
                            SELECT id, username, email, phone, password_hash, created_at FROM users
                            """
                        )
                    )
                    conn.execute(text("DROP TABLE users"))
                    conn.execute(text("ALTER TABLE users_new RENAME TO users"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)"))
                conn.execute(text("PRAGMA foreign_keys=ON"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32)"))
            try:
                conn.execute(text("ALTER TABLE users ALTER COLUMN email DROP NOT NULL"))
            except Exception:  # noqa: BLE001
                pass
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)"))


def _migrate_documents_columns() -> None:
    with engine.begin() as conn:
        if settings.is_sqlite:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(documents)")).fetchall()
            }
            if "index_attempts" not in cols:
                conn.execute(text("ALTER TABLE documents ADD COLUMN index_attempts INTEGER NOT NULL DEFAULT 0"))
            if "updated_at" not in cols:
                conn.execute(text("ALTER TABLE documents ADD COLUMN updated_at DATETIME"))
            if "context_snapshot" not in cols:
                conn.execute(text("ALTER TABLE documents ADD COLUMN context_snapshot JSON"))
            if "embedding_model" not in cols:
                conn.execute(text("ALTER TABLE documents ADD COLUMN embedding_model VARCHAR(128)"))
            if "embedding_version" not in cols:
                conn.execute(text("ALTER TABLE documents ADD COLUMN embedding_version VARCHAR(32)"))
        else:
            conn.execute(
                text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_attempts INTEGER NOT NULL DEFAULT 0")
            )
            conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"))
            conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS context_snapshot JSONB"))
            conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)"))
            conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(32)"))


def _migrate_chat_columns() -> None:
    """Ensure conversations/messages match current model (SQLite create_all won't ALTER)."""
    with engine.begin() as conn:
        if settings.is_sqlite:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            if "messages" in tables:
                cols = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(messages)")).fetchall()
                }
                if "extra" not in cols:
                    conn.execute(text("ALTER TABLE messages ADD COLUMN extra JSON"))
                if "citations" not in cols:
                    conn.execute(text("ALTER TABLE messages ADD COLUMN citations JSON"))
            if "conversations" in tables:
                cols = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(conversations)")).fetchall()
                }
                if "library_ids" not in cols:
                    conn.execute(text("ALTER TABLE conversations ADD COLUMN library_ids JSON"))
                if "updated_at" not in cols:
                    conn.execute(text("ALTER TABLE conversations ADD COLUMN updated_at DATETIME"))
        else:
            conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS extra JSONB"))
            conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS citations JSONB"))
            conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS library_ids JSONB"))
            conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"))
