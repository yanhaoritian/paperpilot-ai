"""Add research-skill attribution and AI token/cost ledger."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_research_skills_usage"
down_revision = "0008_conversation_memory"
branch_labels = None
depends_on = None


def _json_type(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.JSON()


def _tables(bind) -> set[str]:  # noqa: ANN001
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:  # noqa: ANN001
    if table not in _tables(bind):
        return set()
    return {
        str(row["name"])
        for row in sa.inspect(bind).get_indexes(table)
        if row.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    tables = _tables(bind)

    if "model_price_versions" not in tables:
        op.create_table(
            "model_price_versions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column(
                "operation_kind",
                sa.String(length=32),
                nullable=False,
                server_default="chat",
            ),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column(
                "currency",
                sa.String(length=12),
                nullable=False,
                server_default="CNY",
            ),
            sa.Column(
                "input_price_microunits_per_million",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "cached_input_price_microunits_per_million",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "output_price_microunits_per_million",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "per_request_microunits",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "effective_from",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "provider",
                "model",
                "operation_kind",
                "version",
                name="uq_model_price_version",
            ),
        )

    if "ai_usage_events" not in tables:
        op.create_table(
            "ai_usage_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("conversation_id", sa.String(length=36), nullable=True),
            sa.Column("message_id", sa.String(length=36), nullable=True),
            sa.Column("document_id", sa.String(length=36), nullable=True),
            sa.Column("index_job_id", sa.String(length=36), nullable=True),
            sa.Column("skill_id", sa.String(length=64), nullable=True),
            sa.Column("skill_version", sa.String(length=32), nullable=True),
            sa.Column("operation", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column(
                "input_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "cached_input_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "output_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "reasoning_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "total_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "item_count", sa.Integer(), nullable=False, server_default=sa.text("1")
            ),
            sa.Column(
                "usage_source",
                sa.String(length=16),
                nullable=False,
                server_default="provider",
            ),
            sa.Column("price_version", sa.String(length=64), nullable=True),
            sa.Column(
                "currency",
                sa.String(length=12),
                nullable=False,
                server_default="UNPRICED",
            ),
            sa.Column(
                "cost_microunits", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default="success",
            ),
            sa.Column("provider_request_id", sa.String(length=128), nullable=True),
            sa.Column("extra", _json_type(dialect), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    definitions = {
        "model_price_versions": (
            (
                "ix_model_price_lookup",
                ["provider", "model", "operation_kind", "effective_from"],
            ),
        ),
        "ai_usage_events": (
            ("ix_ai_usage_user_created", ["user_id", "created_at"]),
            ("ix_ai_usage_request", ["request_id"]),
            ("ix_ai_usage_operation", ["operation", "created_at"]),
            ("ix_ai_usage_skill", ["skill_id", "created_at"]),
        ),
    }
    for table, indexes in definitions.items():
        existing = _indexes(bind, table)
        for name, columns in indexes:
            if name not in existing:
                op.create_index(name, table, columns)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "ai_usage_events" in tables:
        op.drop_table("ai_usage_events")
    if "model_price_versions" in tables:
        op.drop_table("model_price_versions")
