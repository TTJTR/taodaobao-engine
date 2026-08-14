"""add multi-use invitation redemption slots

Revision ID: 6d23b0c4a911
Revises: f2a14c8e7d90
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d23b0c4a911"
down_revision: str | Sequence[str] | None = "f2a14c8e7d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invitation_redemptions",
        sa.Column("max_uses", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "invitation_redemptions",
        sa.Column("redeemed_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_invitation_redemptions_max_uses",
        "invitation_redemptions",
        "max_uses >= 1 AND max_uses <= 100",
    )
    op.create_check_constraint(
        "ck_invitation_redemptions_redeemed_count",
        "invitation_redemptions",
        "redeemed_count >= 1 AND redeemed_count <= max_uses",
    )
    op.create_table(
        "invitation_redemption_uses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_id_hash", sa.String(length=64), nullable=False),
        sa.Column("redemption_number", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["token_id_hash"],
            ["invitation_redemptions.token_id_hash"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_id_hash", "redemption_number"),
    )
    op.create_index(
        "ix_invitation_redemption_uses_token_id_hash",
        "invitation_redemption_uses",
        ["token_id_hash"],
    )
    op.execute(
        """
        INSERT INTO invitation_redemption_uses
            (id, token_id_hash, redemption_number, consumed_at, created_at)
        SELECT gen_random_uuid(), token_id_hash, 1, consumed_at, created_at
        FROM invitation_redemptions
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_invitation_redemption_uses_token_id_hash",
        table_name="invitation_redemption_uses",
    )
    op.drop_table("invitation_redemption_uses")
    op.drop_constraint(
        "ck_invitation_redemptions_redeemed_count",
        "invitation_redemptions",
        type_="check",
    )
    op.drop_constraint(
        "ck_invitation_redemptions_max_uses",
        "invitation_redemptions",
        type_="check",
    )
    op.drop_column("invitation_redemptions", "redeemed_count")
    op.drop_column("invitation_redemptions", "max_uses")
