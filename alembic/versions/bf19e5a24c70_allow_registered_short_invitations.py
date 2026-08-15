"""allow server-registered short invitation codes

Revision ID: bf19e5a24c70
Revises: 8b42e3f5a6c7
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bf19e5a24c70"
down_revision: str | Sequence[str] | None = "8b42e3f5a6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_invitation_redemptions_redeemed_count",
        "invitation_redemptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_invitation_redemptions_redeemed_count",
        "invitation_redemptions",
        "redeemed_count >= 0 AND redeemed_count <= max_uses",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM invitation_redemptions WHERE redeemed_count = 0"
    )
    op.drop_constraint(
        "ck_invitation_redemptions_redeemed_count",
        "invitation_redemptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_invitation_redemptions_redeemed_count",
        "invitation_redemptions",
        "redeemed_count >= 1 AND redeemed_count <= max_uses",
    )
