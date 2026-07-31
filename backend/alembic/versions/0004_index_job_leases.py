"""Add renewable leases to durable index jobs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_index_job_leases"
down_revision = "0003_worker_heartbeat"
branch_labels = None
depends_on = None


def _index_names(bind) -> set[str]:  # noqa: ANN001
    inspector = sa.inspect(bind)
    if "index_jobs" not in inspector.get_table_names():
        return set()
    return {
        str(index["name"])
        for index in inspector.get_indexes("index_jobs")
        if index.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        str(column["name"])
        for column in inspector.get_columns("index_jobs")
    }
    with op.batch_alter_table("index_jobs") as batch:
        if "lease_owner" not in columns:
            batch.add_column(sa.Column("lease_owner", sa.String(length=128), nullable=True))
        if "lease_expires_at" not in columns:
            batch.add_column(
                sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
            )

    indexes = _index_names(bind)
    if "ix_index_jobs_lease_owner" not in indexes:
        op.create_index(
            "ix_index_jobs_lease_owner",
            "index_jobs",
            ["lease_owner"],
        )
    if "ix_index_jobs_lease_expires_at" not in indexes:
        op.create_index(
            "ix_index_jobs_lease_expires_at",
            "index_jobs",
            ["lease_expires_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_index_jobs_lease_expires_at", table_name="index_jobs")
    op.drop_index("ix_index_jobs_lease_owner", table_name="index_jobs")
    with op.batch_alter_table("index_jobs") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
