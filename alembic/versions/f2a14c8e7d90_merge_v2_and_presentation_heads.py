"""merge V2 business and presentation renderer migration heads

Revision ID: f2a14c8e7d90
Revises: e5a9f3b2c018, 712f72264fd8
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

revision: str = "f2a14c8e7d90"
down_revision: str | Sequence[str] | None = ("e5a9f3b2c018", "712f72264fd8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the independently developed V2 and presentation histories."""


def downgrade() -> None:
    """Restore the two independent migration heads."""
