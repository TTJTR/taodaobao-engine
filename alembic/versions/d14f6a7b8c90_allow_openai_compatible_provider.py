"""allow custom OpenAI-compatible workspace providers

Revision ID: d14f6a7b8c90
Revises: c931f6d7e8a9
Create Date: 2026-09-06 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d14f6a7b8c90"
down_revision: str | Sequence[str] | None = "c931f6d7e8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_workspace_model_connections_provider"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "workspace_model_connections", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "workspace_model_connections",
        "provider IN ('dashscope', 'deepseek', 'openai-compatible')",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM workspace_model_connections WHERE provider = 'openai-compatible'"
    )
    op.drop_constraint(CONSTRAINT, "workspace_model_connections", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "workspace_model_connections",
        "provider IN ('dashscope', 'deepseek')",
    )
