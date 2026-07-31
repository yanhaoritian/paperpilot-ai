"""Add hierarchical, retrievable conversation memory.

The revision is idempotent because the adopted 0002 baseline imports current
model metadata when constructing a fresh database.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_conversation_memory"
down_revision = "0007_postgres_trigram_search"
branch_labels = None
depends_on = None


def _json_type(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.JSON()


def _embedding_type(dialect: str):
    if dialect == "postgresql":
        from pgvector.sqlalchemy import Vector

        from app.config import get_settings

        return Vector(get_settings().embedding_dimensions)
    return sa.JSON()


def _column_names(bind, table: str) -> set[str]:  # noqa: ANN001
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {
        str(column["name"])
        for column in inspector.get_columns(table)
    }


def _index_names(bind, table: str) -> set[str]:  # noqa: ANN001
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {
        str(index["name"])
        for index in inspector.get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    message_columns = {
        str(column["name"]): column
        for column in sa.inspect(bind).get_columns("messages")
    }
    sequence_was_added = "sequence" not in message_columns
    if sequence_was_added:
        with op.batch_alter_table("messages") as batch:
            batch.add_column(
                sa.Column("sequence", sa.Integer(), nullable=True)
            )
        if dialect == "postgresql":
            op.execute(
                """
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY conversation_id
                        ORDER BY created_at ASC,
                            CASE role WHEN 'user' THEN 0 ELSE 1 END ASC,
                            id ASC
                    ) AS seq
                    FROM messages
                )
                UPDATE messages
                SET sequence = ranked.seq
                FROM ranked
                WHERE messages.id = ranked.id
                  AND messages.sequence IS NULL
                """
            )
        else:
            op.execute(
                """
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY conversation_id
                        ORDER BY created_at ASC,
                            CASE role WHEN 'user' THEN 0 ELSE 1 END ASC,
                            id ASC
                    ) AS seq
                    FROM messages
                )
                UPDATE messages
                SET sequence = (
                    SELECT seq FROM ranked WHERE ranked.id = messages.id
                )
                WHERE sequence IS NULL
                """
            )
        with op.batch_alter_table("messages") as batch:
            batch.alter_column(
                "sequence",
                existing_type=sa.Integer(),
                nullable=False,
            )
    message_indexes = _index_names(bind, "messages")
    if "uq_messages_conversation_sequence" not in message_indexes:
        op.create_index(
            "uq_messages_conversation_sequence",
            "messages",
            ["conversation_id", "sequence"],
            unique=True,
        )

    conversation_columns = _column_names(bind, "conversations")
    with op.batch_alter_table("conversations") as batch:
        if "memory_enabled" not in conversation_columns:
            batch.add_column(
                sa.Column(
                    "memory_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )
        if "memory_summary" not in conversation_columns:
            batch.add_column(sa.Column("memory_summary", sa.Text(), nullable=True))
        if "summarized_message_count" not in conversation_columns:
            batch.add_column(
                sa.Column(
                    "summarized_message_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "memory_revision" not in conversation_columns:
            batch.add_column(
                sa.Column(
                    "memory_revision",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "memory_updated_at" not in conversation_columns:
            batch.add_column(
                sa.Column(
                    "memory_updated_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )

    inspector = sa.inspect(bind)
    if "conversation_memories" not in inspector.get_table_names():
        op.create_table(
            "conversation_memories",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "owner_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "conversation_id",
                sa.String(length=36),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "kind",
                sa.String(length=32),
                nullable=False,
                server_default="episode",
            ),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("source_start_index", sa.Integer(), nullable=False),
            sa.Column("source_end_index", sa.Integer(), nullable=False),
            sa.Column(
                "importance",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0.5"),
            ),
            sa.Column("embedding_model", sa.String(length=128), nullable=True),
            sa.Column("embedding_version", sa.String(length=32), nullable=True),
            sa.Column("embedding", _embedding_type(dialect), nullable=True),
            sa.Column("extra", _json_type(dialect), nullable=True),
            sa.Column(
                "access_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "last_accessed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "conversation_id",
                "source_start_index",
                "source_end_index",
                name="uq_conversation_memory_range",
            ),
        )

    indexes = _index_names(bind, "conversation_memories")
    definitions = (
        ("ix_conversation_memories_owner_id", ["owner_id"]),
        ("ix_conversation_memories_conversation_id", ["conversation_id"]),
        ("ix_conversation_memories_embedding_version", ["embedding_version"]),
        (
            "ix_conversation_memories_owner_conversation",
            ["owner_id", "conversation_id"],
        ),
    )
    for name, columns in definitions:
        if name not in indexes:
            op.create_index(name, "conversation_memories", columns)

    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        with op.get_context().autocommit_block():
            op.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_conversation_memories_embedding_hnsw
                ON conversation_memories
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
                """
            )
            op.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_conversation_memories_content_trgm
                ON conversation_memories
                USING gist (content gist_trgm_ops)
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "ix_conversation_memories_content_trgm"
            )
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "ix_conversation_memories_embedding_hnsw"
            )
    if "conversation_memories" in sa.inspect(bind).get_table_names():
        op.drop_table("conversation_memories")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("memory_updated_at")
        batch.drop_column("memory_revision")
        batch.drop_column("summarized_message_count")
        batch.drop_column("memory_summary")
        batch.drop_column("memory_enabled")
    indexes = _index_names(bind, "messages")
    if "uq_messages_conversation_sequence" in indexes:
        op.drop_index(
            "uq_messages_conversation_sequence",
            table_name="messages",
        )
    if "sequence" in _column_names(bind, "messages"):
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("sequence")
