"""Add durable worker heartbeat records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_worker_heartbeat"
down_revision = "0002_current_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "worker_heartbeats" not in inspector.get_table_names():
        op.create_table(
            "worker_heartbeats",
            sa.Column("worker_id", sa.String(length=128), primary_key=True),
            sa.Column(
                "worker_kind",
                sa.String(length=32),
                nullable=False,
                server_default="index",
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("extra", sa.JSON(), nullable=True),
        )
        op.create_index(
            "ix_worker_heartbeats_worker_kind",
            "worker_heartbeats",
            ["worker_kind"],
        )
        op.create_index(
            "ix_worker_heartbeats_last_seen_at",
            "worker_heartbeats",
            ["last_seen_at"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_heartbeats_last_seen_at",
        table_name="worker_heartbeats",
    )
    op.drop_index(
        "ix_worker_heartbeats_worker_kind",
        table_name="worker_heartbeats",
    )
    op.drop_table("worker_heartbeats")
