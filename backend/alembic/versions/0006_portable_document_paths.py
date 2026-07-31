"""Normalize stored PDF paths so databases are portable across hosts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_portable_document_paths"
down_revision = "0005_schema_convergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, owner_id, library_id, file_path "
            "FROM documents"
        )
    ).mappings()
    for row in rows:
        document_id = str(row["id"])
        owner_id = str(row["owner_id"])
        library_id = str(row["library_id"])
        current = str(row["file_path"] or "")
        normalized = current.replace("\\", "/")
        portable = f"{owner_id}/{library_id}/{document_id}.pdf"
        if normalized.lower().endswith(portable.lower()) and current != portable:
            bind.execute(
                sa.text(
                    "UPDATE documents SET file_path = :file_path WHERE id = :document_id"
                ),
                {"file_path": portable, "document_id": document_id},
            )


def downgrade() -> None:
    # Absolute host paths cannot be reconstructed portably.
    pass
