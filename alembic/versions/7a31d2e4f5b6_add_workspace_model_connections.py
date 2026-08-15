"""add workspace model connections

Revision ID: 7a31d2e4f5b6
Revises: 6d23b0c4a911
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7a31d2e4f5b6"
down_revision: str | Sequence[str] | None = "6d23b0c4a911"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_model_connections",
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("updated_by_id", sa.UUID(), nullable=False),
        sa.Column("last_test_status", sa.String(length=32), server_default="ok", nullable=False),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint(
            "capability IN ('ai', 'interactive-html')",
            name="ck_workspace_model_connections_capability",
        ),
        sa.CheckConstraint(
            "provider IN ('dashscope', 'deepseek')",
            name="ck_workspace_model_connections_provider",
        ),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "capability", name="uq_workspace_model_connections_capability"
        ),
    )
    op.create_index(
        "ix_workspace_model_connections_workspace_id",
        "workspace_model_connections",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_model_connections_is_deleted",
        "workspace_model_connections",
        ["is_deleted"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_model_connections_is_deleted",
        table_name="workspace_model_connections",
    )
    op.drop_index(
        "ix_workspace_model_connections_workspace_id",
        table_name="workspace_model_connections",
    )
    op.drop_table("workspace_model_connections")
