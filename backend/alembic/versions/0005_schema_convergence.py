"""Converge adopted PostgreSQL schemas with current model metadata.

Older installations were created with ``create_all`` and can therefore have
equivalent constraints under different names, missing secondary indexes, or a
mix of JSON and JSONB columns. This revision normalizes those differences.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_schema_convergence"
down_revision = "0004_index_job_leases"
branch_labels = None
depends_on = None


INDEXES: dict[str, tuple[tuple[str, tuple[str, ...], bool], ...]] = {
    "users": (
        ("ix_users_username", ("username",), True),
        ("ix_users_email", ("email",), True),
        ("ix_users_phone", ("phone",), True),
    ),
    "usage_daily": (
        ("ix_usage_daily_user_id", ("user_id",), False),
        ("ix_usage_daily_day", ("day",), False),
    ),
    "auth_codes": (("ix_auth_codes_target", ("target",), False),),
    "libraries": (("ix_libraries_owner_id", ("owner_id",), False),),
    "documents": (
        ("ix_documents_library_id", ("library_id",), False),
        ("ix_documents_owner_id", ("owner_id",), False),
        ("ix_documents_file_hash", ("file_hash",), False),
        ("ix_documents_status", ("status",), False),
        ("ix_documents_embedding_version", ("embedding_version",), False),
    ),
    "blocks": (
        ("ix_blocks_document_id", ("document_id",), False),
        ("ix_blocks_library_id", ("library_id",), False),
        ("ix_blocks_owner_id", ("owner_id",), False),
        ("ix_blocks_role", ("role",), False),
    ),
    "chunks": (
        ("ix_chunks_document_id", ("document_id",), False),
        ("ix_chunks_library_id", ("library_id",), False),
        ("ix_chunks_owner_id", ("owner_id",), False),
        ("ix_chunks_collection_key", ("collection_key",), False),
        ("ix_chunks_embedding_version", ("embedding_version",), False),
        ("ix_chunks_owner_library", ("owner_id", "library_id"), False),
    ),
    "index_jobs": (
        ("ix_index_jobs_document_id", ("document_id",), False),
        ("ix_index_jobs_owner_id", ("owner_id",), False),
        ("ix_index_jobs_status", ("status",), False),
        ("ix_index_jobs_lease_owner", ("lease_owner",), False),
        ("ix_index_jobs_lease_expires_at", ("lease_expires_at",), False),
    ),
    "worker_heartbeats": (
        ("ix_worker_heartbeats_worker_kind", ("worker_kind",), False),
        ("ix_worker_heartbeats_last_seen_at", ("last_seen_at",), False),
    ),
    "conversations": (("ix_conversations_owner_id", ("owner_id",), False),),
    "messages": (("ix_messages_conversation_id", ("conversation_id",), False),),
}

JSON_COLUMNS: dict[str, tuple[str, ...]] = {
    "documents": ("context_snapshot",),
    "blocks": ("bbox", "extra"),
    "chunks": ("block_ids", "extra"),
    "worker_heartbeats": ("extra",),
    "conversations": ("library_ids",),
    "messages": ("citations", "extra"),
}

NOT_NULL_DEFAULTS: dict[str, tuple[tuple[str, str], ...]] = {
    "users": (("created_at", "CURRENT_TIMESTAMP"),),
    "libraries": (("created_at", "CURRENT_TIMESTAMP"),),
    "documents": (
        ("status", "'pending'"),
        ("page_count", "0"),
        ("created_at", "CURRENT_TIMESTAMP"),
    ),
    "conversations": (("created_at", "CURRENT_TIMESTAMP"),),
    "messages": (("created_at", "CURRENT_TIMESTAMP"),),
}


def _ensure_indexes(bind) -> None:  # noqa: ANN001
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, definitions in INDEXES.items():
        if table not in tables:
            continue
        existing = {
            str(index["name"])
            for index in inspector.get_indexes(table)
            if index.get("name")
        }
        columns = {
            str(column["name"])
            for column in inspector.get_columns(table)
        }
        for name, index_columns, unique in definitions:
            if name in existing or not set(index_columns).issubset(columns):
                continue
            op.create_index(name, table, list(index_columns), unique=unique)
            existing.add(name)


def _normalize_postgres_json(bind) -> None:  # noqa: ANN001
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, columns in JSON_COLUMNS.items():
        if table not in tables:
            continue
        types = {
            str(column["name"]): column["type"]
            for column in inspector.get_columns(table)
        }
        for column in columns:
            column_type = types.get(column)
            if column_type is None or column_type.__class__.__name__.upper() == "JSONB":
                continue
            quoted_table = bind.dialect.identifier_preparer.quote(table)
            quoted_column = bind.dialect.identifier_preparer.quote(column)
            op.execute(
                sa.text(
                    f"ALTER TABLE {quoted_table} "
                    f"ALTER COLUMN {quoted_column} TYPE JSONB "
                    f"USING {quoted_column}::jsonb"
                )
            )


def _normalize_postgres_nullability(bind) -> None:  # noqa: ANN001
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, definitions in NOT_NULL_DEFAULTS.items():
        if table not in tables:
            continue
        columns = {
            str(column["name"]): column
            for column in inspector.get_columns(table)
        }
        for column, default_sql in definitions:
            info = columns.get(column)
            if info is None or not info.get("nullable"):
                continue
            quoted_table = bind.dialect.identifier_preparer.quote(table)
            quoted_column = bind.dialect.identifier_preparer.quote(column)
            op.execute(
                sa.text(
                    f"UPDATE {quoted_table} SET {quoted_column} = {default_sql} "
                    f"WHERE {quoted_column} IS NULL"
                )
            )
            op.execute(
                sa.text(
                    f"ALTER TABLE {quoted_table} "
                    f"ALTER COLUMN {quoted_column} SET NOT NULL"
                )
            )


def _normalize_postgres_user_uniques(bind) -> None:  # noqa: ANN001
    inspector = sa.inspect(bind)
    if "users" not in inspector.get_table_names():
        return
    for constraint in inspector.get_unique_constraints("users"):
        columns = tuple(str(column) for column in constraint.get("column_names") or ())
        name = constraint.get("name")
        if name and columns in {("username",), ("email",), ("phone",)}:
            op.drop_constraint(str(name), "users", type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    # Create replacement unique indexes before removing equivalent legacy
    # constraints, so uniqueness is never lost during the transaction.
    _ensure_indexes(bind)
    if bind.dialect.name == "postgresql":
        _normalize_postgres_json(bind)
        _normalize_postgres_nullability(bind)
        _normalize_postgres_user_uniques(bind)


def downgrade() -> None:
    # This is an adoption/convergence revision. Removing performance indexes or
    # weakening normalized constraints would be destructive, so downgrade is
    # intentionally data-preserving.
    pass
