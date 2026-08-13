"""add tender requirement structure fields

Revision ID: e5a9f3b2c018
Revises: c4d8e2f1a907
Create Date: 2026-08-13 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5a9f3b2c018"
down_revision: str | None = "c4d8e2f1a907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tender_requirements",
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "tender_requirements",
        sa.Column("recommended_action", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tender_requirements", "recommended_action")
    op.drop_column("tender_requirements", "metrics")
