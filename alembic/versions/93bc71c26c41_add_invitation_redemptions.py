"""add invitation redemptions

Revision ID: 93bc71c26c41
Revises: 4859effd0277
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "93bc71c26c41"
down_revision: str | Sequence[str] | None = "4859effd0277"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitation_redemptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_id_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_id_hash"),
    )
    op.create_index("ix_invitation_redemptions_expires_at", "invitation_redemptions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_invitation_redemptions_expires_at", table_name="invitation_redemptions")
    op.drop_table("invitation_redemptions")
