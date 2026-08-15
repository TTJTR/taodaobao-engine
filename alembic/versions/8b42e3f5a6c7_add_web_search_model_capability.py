"""add web search model capability

Revision ID: 8b42e3f5a6c7
Revises: 7a31d2e4f5b6
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8b42e3f5a6c7"
down_revision: str | Sequence[str] | None = "7a31d2e4f5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_workspace_model_connections_capability"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "workspace_model_connections", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "workspace_model_connections",
        "capability IN ('ai', 'interactive-html', 'web-search')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace_model_connections WHERE capability = 'web-search'")
    op.drop_constraint(CONSTRAINT, "workspace_model_connections", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "workspace_model_connections",
        "capability IN ('ai', 'interactive-html')",
    )
