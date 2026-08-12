"""add customer profile versions

Revision ID: e62a1c4b09d3
Revises: d57e3a8c91b4
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e62a1c4b09d3"
down_revision: str | Sequence[str] | None = "d57e3a8c91b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_profiles",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_table(
        "customer_profile_versions",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("proposal_id", sa.UUID()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by_id", sa.UUID(), nullable=False),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("profile_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_id"], ["customer_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["profile_intelligence_proposals.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "version", name="uq_customer_profile_version"),
    )
    op.create_index(
        "ix_customer_profile_versions_profile",
        "customer_profile_versions",
        ["profile_id", "version"],
    )
    op.create_index(
        "ix_customer_profile_versions_workspace_id",
        "customer_profile_versions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_customer_profile_versions_is_deleted",
        "customer_profile_versions",
        ["is_deleted"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_profile_versions_is_deleted", table_name="customer_profile_versions")
    op.drop_index(
        "ix_customer_profile_versions_workspace_id",
        table_name="customer_profile_versions",
    )
    op.drop_index("ix_customer_profile_versions_profile", table_name="customer_profile_versions")
    op.drop_table("customer_profile_versions")
    op.drop_column("customer_profiles", "version")
