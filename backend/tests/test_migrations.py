from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND / "alembic.ini"


def _upgrade(db_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["JWT_REQUIRE_STRONG"] = "0"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI),
            "upgrade",
            "head",
        ],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_alembic_creates_current_schema_on_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    _upgrade(db_path)
    with sqlite3.connect(db_path) as db:
        version = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert version == "0009_research_skills_usage"
    assert {
        "users",
        "libraries",
        "documents",
        "blocks",
        "chunks",
        "index_jobs",
        "conversations",
        "messages",
        "usage_daily",
        "auth_codes",
        "worker_heartbeats",
        "conversation_memories",
        "ai_usage_events",
        "model_price_versions",
    }.issubset(tables)


def test_alembic_adopts_legacy_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE users (
              id VARCHAR(36) PRIMARY KEY,
              username VARCHAR(64) NOT NULL UNIQUE,
              email VARCHAR(255) NOT NULL UNIQUE,
              password_hash VARCHAR(255) NOT NULL,
              created_at DATETIME
            );
            CREATE TABLE libraries (
              id VARCHAR(36) PRIMARY KEY,
              owner_id VARCHAR(36) NOT NULL,
              name VARCHAR(200) NOT NULL,
              description TEXT,
              created_at DATETIME
            );
            CREATE TABLE documents (
              id VARCHAR(36) PRIMARY KEY,
              library_id VARCHAR(36) NOT NULL,
              owner_id VARCHAR(36) NOT NULL,
              file_name VARCHAR(512) NOT NULL,
              file_path VARCHAR(1024) NOT NULL,
              file_hash VARCHAR(64) NOT NULL,
              status VARCHAR(32),
              status_detail TEXT,
              page_count INTEGER,
              created_at DATETIME
            );
            CREATE TABLE chunks (
              id VARCHAR(36) PRIMARY KEY,
              document_id VARCHAR(36) NOT NULL,
              library_id VARCHAR(36) NOT NULL,
              owner_id VARCHAR(36) NOT NULL,
              chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              page_start INTEGER,
              page_end INTEGER,
              embedding JSON
            );
            CREATE TABLE conversations (
              id VARCHAR(36) PRIMARY KEY,
              owner_id VARCHAR(36) NOT NULL,
              title VARCHAR(200) NOT NULL,
              created_at DATETIME
            );
            CREATE TABLE messages (
              id VARCHAR(36) PRIMARY KEY,
              conversation_id VARCHAR(36) NOT NULL,
              role VARCHAR(16) NOT NULL,
              content TEXT NOT NULL,
              created_at DATETIME
            );
            INSERT INTO users (id, username, email, password_hash)
            VALUES ('user-1', 'legacy-user', 'legacy@example.com', 'hash');
            INSERT INTO libraries (id, owner_id, name)
            VALUES ('library-1', 'user-1', 'Legacy Library');
            INSERT INTO documents (
              id, library_id, owner_id, file_name, file_path, file_hash,
              status, page_count
            ) VALUES (
              'document-1', 'library-1', 'user-1', 'legacy.pdf',
              'C:\\old\\data\\pdfs\\user-1\\library-1\\document-1.pdf',
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              'ready', 1
            );
            INSERT INTO conversations (id, owner_id, title)
            VALUES ('conversation-1', 'user-1', 'Legacy conversation');
            INSERT INTO messages (
              id, conversation_id, role, content, created_at
            ) VALUES (
              'message-assistant', 'conversation-1', 'assistant',
              'Legacy answer', '2026-01-01 00:00:00'
            );
            INSERT INTO messages (
              id, conversation_id, role, content, created_at
            ) VALUES (
              'message-user', 'conversation-1', 'user',
              'Legacy question', '2026-01-01 00:00:00'
            );
            """
        )
    _upgrade(db_path)
    with sqlite3.connect(db_path) as db:
        user_cols = {row[1]: row for row in db.execute("PRAGMA table_info(users)")}
        doc_cols = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        chunk_cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        conv_cols = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
        message_cols = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        job_cols = {row[1] for row in db.execute("PRAGMA table_info(index_jobs)")}
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        portable_path = db.execute(
            "SELECT file_path FROM documents WHERE id='document-1'"
        ).fetchone()[0]
        ordered_messages = db.execute(
            "SELECT role, sequence FROM messages "
            "WHERE conversation_id='conversation-1' ORDER BY sequence"
        ).fetchall()
    assert "phone" in user_cols
    assert user_cols["email"][3] == 0  # nullable after batch migration
    assert {"index_attempts", "context_snapshot", "embedding_version"} <= doc_cols
    assert {"section_path", "context_prefix", "collection_key"} <= chunk_cols
    assert {
        "library_ids",
        "updated_at",
        "memory_enabled",
        "memory_summary",
        "summarized_message_count",
        "memory_revision",
        "memory_updated_at",
    } <= conv_cols
    assert {"citations", "extra", "sequence"} <= message_cols
    assert {"lease_owner", "lease_expires_at"} <= job_cols
    assert portable_path == "user-1/library-1/document-1.pdf"
    assert {
        "blocks",
        "index_jobs",
        "usage_daily",
        "auth_codes",
        "worker_heartbeats",
        "conversation_memories",
        "ai_usage_events",
        "model_price_versions",
    } <= tables
    assert ordered_messages == [("user", 1), ("assistant", 2)]
