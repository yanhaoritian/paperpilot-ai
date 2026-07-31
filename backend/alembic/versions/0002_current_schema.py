"""Adopt or create the current PaperPilot schema.

This migration is intentionally idempotent at the table/column/index level so
an existing create_all-based installation can be stamped by running upgrade,
while an empty database receives the full current schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_current_schema"
down_revision = "0001_placeholder"
branch_labels = None
depends_on = None


def _json_type(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.JSON()


def _column_names(bind, table: str) -> set[str]:  # noqa: ANN001
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {str(col["name"]) for col in inspector.get_columns(table)}


def _add_missing_columns(bind, table: str, columns: list[sa.Column]) -> None:  # noqa: ANN001
    existing = _column_names(bind, table)
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)
            existing.add(str(column.name))


def _ensure_index(
    bind,  # noqa: ANN001
    table: str,
    name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    names = {str(idx["name"]) for idx in inspector.get_indexes(table) if idx.get("name")}
    if name not in names:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Creates all tables that do not exist. Existing legacy tables are then
    # upgraded column-by-column below.
    from app import models  # noqa: F401
    from app.db import Base

    Base.metadata.create_all(bind=bind, checkfirst=True)
    json_type = _json_type(dialect)

    user_columns = {col["name"]: col for col in sa.inspect(bind).get_columns("users")}
    phone_missing = "phone" not in user_columns
    email_not_nullable = bool(user_columns.get("email") and not user_columns["email"]["nullable"])
    if phone_missing or email_not_nullable:
        with op.batch_alter_table("users") as batch:
            if phone_missing:
                batch.add_column(sa.Column("phone", sa.String(length=32), nullable=True))
            if email_not_nullable:
                batch.alter_column(
                    "email",
                    existing_type=sa.String(length=255),
                    nullable=True,
                )

    _add_missing_columns(
        bind,
        "documents",
        [
            sa.Column(
                "index_attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("context_snapshot", json_type, nullable=True),
            sa.Column("embedding_model", sa.String(length=128), nullable=True),
            sa.Column("embedding_version", sa.String(length=32), nullable=True),
        ],
    )
    _add_missing_columns(
        bind,
        "messages",
        [
            sa.Column("extra", _json_type(dialect), nullable=True),
            sa.Column("citations", _json_type(dialect), nullable=True),
        ],
    )
    _add_missing_columns(
        bind,
        "conversations",
        [
            sa.Column(
                "library_ids",
                _json_type(dialect),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        ],
    )
    _add_missing_columns(
        bind,
        "chunks",
        [
            sa.Column("section_path", sa.String(length=512), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=True),
            sa.Column("context_prefix", sa.Text(), nullable=True),
            sa.Column("block_ids", _json_type(dialect), nullable=True),
            sa.Column("extra", _json_type(dialect), nullable=True),
            sa.Column("collection_key", sa.String(length=128), nullable=True),
            sa.Column("embedding_model", sa.String(length=128), nullable=True),
            sa.Column("embedding_version", sa.String(length=32), nullable=True),
        ],
    )

    _ensure_index(bind, "users", "ix_users_phone", ["phone"], unique=True)
    _ensure_index(
        bind,
        "documents",
        "ix_documents_embedding_version",
        ["embedding_version"],
    )
    _ensure_index(bind, "chunks", "ix_chunks_collection_key", ["collection_key"])
    _ensure_index(bind, "chunks", "ix_chunks_embedding_version", ["embedding_version"])
    _ensure_index(
        bind,
        "chunks",
        "ix_chunks_owner_library",
        ["owner_id", "library_id"],
    )


def downgrade() -> None:
    # Baseline adoption must never drop pre-existing user tables. Future
    # revisions should provide normal reversible downgrade operations.
    pass
