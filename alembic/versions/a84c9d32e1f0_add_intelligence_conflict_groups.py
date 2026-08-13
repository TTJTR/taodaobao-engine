"""add intelligence conflict groups

Revision ID: a84c9d32e1f0
Revises: f73b2d5c10e4
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a84c9d32e1f0"
down_revision: str | Sequence[str] | None = "f73b2d5c10e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intelligence_items",
        sa.Column("conflict_group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_intelligence_items_conflict_group",
        "intelligence_items",
        ["conflict_group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_intelligence_items_conflict_group", table_name="intelligence_items")
    op.drop_column("intelligence_items", "conflict_group_id")
