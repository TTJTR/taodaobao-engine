"""add intelligence snapshot references to downstream runs

Revision ID: c4d8e2f1a907
Revises: b2f4e7c9d1a6
Create Date: 2026-08-13 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4d8e2f1a907"
down_revision: str | None = "b2f4e7c9d1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_runs",
        sa.Column("intelligence_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_solution_runs_intelligence_snapshot",
        "solution_runs",
        "intelligence_snapshots",
        ["intelligence_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_solution_runs_intelligence_snapshot_id",
        "solution_runs",
        ["intelligence_snapshot_id"],
    )
    op.add_column(
        "research_tasks",
        sa.Column("intelligence_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_research_tasks_intelligence_snapshot",
        "research_tasks",
        "intelligence_snapshots",
        ["intelligence_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_research_tasks_intelligence_snapshot_id",
        "research_tasks",
        ["intelligence_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_tasks_intelligence_snapshot_id", table_name="research_tasks")
    op.drop_constraint(
        "fk_research_tasks_intelligence_snapshot",
        "research_tasks",
        type_="foreignkey",
    )
    op.drop_column("research_tasks", "intelligence_snapshot_id")
    op.drop_index("ix_solution_runs_intelligence_snapshot_id", table_name="solution_runs")
    op.drop_constraint(
        "fk_solution_runs_intelligence_snapshot",
        "solution_runs",
        type_="foreignkey",
    )
    op.drop_column("solution_runs", "intelligence_snapshot_id")
