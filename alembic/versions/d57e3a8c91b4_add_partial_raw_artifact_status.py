"""add partial raw artifact status

Revision ID: d57e3a8c91b4
Revises: c41d9e2f7a10
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d57e3a8c91b4"
down_revision: str | Sequence[str] | None = "c41d9e2f7a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "raw_artifact_status"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "raw_artifacts", type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "raw_artifacts",
        "status IN ('captured', 'partial', 'validated', 'rejected', 'unavailable')",
    )


def downgrade() -> None:
    op.execute("UPDATE raw_artifacts SET status = 'captured' WHERE status = 'partial'")
    op.drop_constraint(CONSTRAINT_NAME, "raw_artifacts", type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "raw_artifacts",
        "status IN ('captured', 'validated', 'rejected', 'unavailable')",
    )
