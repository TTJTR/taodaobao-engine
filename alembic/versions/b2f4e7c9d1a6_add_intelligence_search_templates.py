"""add intelligence search templates

Revision ID: b2f4e7c9d1a6
Revises: d3e7f1a9c2b4
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2f4e7c9d1a6"
down_revision: str | Sequence[str] | None = "d3e7f1a9c2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intelligence_search_templates",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("query_template", sa.Text(), nullable=False),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_intelligence_search_template_name"),
    )
    op.create_index(
        "ix_intelligence_search_templates_workspace_purpose",
        "intelligence_search_templates",
        ["workspace_id", "purpose"],
    )
    op.create_index(
        op.f("ix_intelligence_search_templates_is_deleted"),
        "intelligence_search_templates",
        ["is_deleted"],
    )
    op.create_index(
        op.f("ix_intelligence_search_templates_workspace_id"),
        "intelligence_search_templates",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_intelligence_search_templates_workspace_id"),
        table_name="intelligence_search_templates",
    )
    op.drop_index(
        op.f("ix_intelligence_search_templates_is_deleted"),
        table_name="intelligence_search_templates",
    )
    op.drop_index(
        "ix_intelligence_search_templates_workspace_purpose",
        table_name="intelligence_search_templates",
    )
    op.drop_table("intelligence_search_templates")
