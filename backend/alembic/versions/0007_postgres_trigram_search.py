"""Add an indexed PostgreSQL sparse-retrieval candidate path."""

from __future__ import annotations

from alembic import op

revision = "0007_postgres_trigram_search"
down_revision = "0006_portable_document_paths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chunks_text_trgm
            ON chunks USING gist (text gist_trgm_ops)
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_chunks_text_trgm"
        )
