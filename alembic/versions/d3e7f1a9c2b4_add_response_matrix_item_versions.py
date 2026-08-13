"""add response matrix item versions

Revision ID: d3e7f1a9c2b4
Revises: a84c9d32e1f0
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3e7f1a9c2b4"
down_revision: str | Sequence[str] | None = "a84c9d32e1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "response_matrix_item_versions",
        sa.Column("response_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("item_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["response_item_id"], ["response_matrix_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("response_item_id", "version", name="uq_response_matrix_item_version"),
    )
    op.create_index(
        "ix_response_matrix_item_versions_item",
        "response_matrix_item_versions",
        ["response_item_id", "version"],
    )
    op.create_index(
        op.f("ix_response_matrix_item_versions_is_deleted"),
        "response_matrix_item_versions",
        ["is_deleted"],
    )
    op.create_index(
        op.f("ix_response_matrix_item_versions_workspace_id"),
        "response_matrix_item_versions",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_response_matrix_item_versions_workspace_id"),
        table_name="response_matrix_item_versions",
    )
    op.drop_index(
        op.f("ix_response_matrix_item_versions_is_deleted"),
        table_name="response_matrix_item_versions",
    )
    op.drop_index(
        "ix_response_matrix_item_versions_item",
        table_name="response_matrix_item_versions",
    )
    op.drop_table("response_matrix_item_versions")
