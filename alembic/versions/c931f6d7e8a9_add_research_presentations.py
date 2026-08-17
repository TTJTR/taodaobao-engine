"""allow interactive presentations from deep research

Revision ID: c931f6d7e8a9
Revises: bf19e5a24c70
Create Date: 2026-08-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c931f6d7e8a9"
down_revision: str | Sequence[str] | None = "bf19e5a24c70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("presentation_runs", "solution_run_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column(
        "presentation_runs", "solution_version", existing_type=sa.Integer(), nullable=True
    )
    op.alter_column(
        "presentation_runs", "upstream_trust_version", existing_type=sa.Integer(), nullable=True
    )
    op.add_column("presentation_runs", sa.Column("research_task_id", sa.UUID(), nullable=True))
    op.add_column(
        "presentation_runs", sa.Column("upstream_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_foreign_key(
        "fk_presentation_runs_research_task_id_research_tasks",
        "presentation_runs",
        "research_tasks",
        ["research_task_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_presentation_research_status",
        "presentation_runs",
        ["research_task_id", "status"],
    )
    op.create_check_constraint(
        "ck_presentation_exactly_one_upstream",
        "presentation_runs",
        "(solution_run_id IS NOT NULL AND research_task_id IS NULL) OR "
        "(solution_run_id IS NULL AND research_task_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_presentation_exactly_one_upstream", "presentation_runs", type_="check")
    op.drop_index("ix_presentation_research_status", table_name="presentation_runs")
    op.drop_constraint(
        "fk_presentation_runs_research_task_id_research_tasks",
        "presentation_runs",
        type_="foreignkey",
    )
    op.drop_column("presentation_runs", "upstream_fingerprint")
    op.drop_column("presentation_runs", "research_task_id")
    op.alter_column(
        "presentation_runs", "upstream_trust_version", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column(
        "presentation_runs", "solution_version", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column("presentation_runs", "solution_run_id", existing_type=sa.UUID(), nullable=False)
