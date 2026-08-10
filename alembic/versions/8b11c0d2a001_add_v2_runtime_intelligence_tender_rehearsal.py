"""add V2 runtime, intelligence, tender and rehearsal models

Revision ID: 8b11c0d2a001
Revises: 509d9268f418
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8b11c0d2a001"
down_revision: str | Sequence[str] | None = "509d9268f418"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def entity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
    ]


def indexes(table: str) -> None:
    op.create_index(f"ix_{table}_is_deleted", table, ["is_deleted"])
    op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])


def enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.create_table(
        "search_runs",
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column(
            "status",
            enum(
                "search_run_status",
                "queued",
                "running",
                "completed",
                "partial",
                "failed",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("input_snapshot", JSONB, nullable=False),
        sa.Column("result_summary", JSONB, nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(1000)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *entity_columns(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes("search_runs")
    op.create_index("ix_search_runs_workspace_status", "search_runs", ["workspace_id", "status"])

    op.create_table(
        "intelligence_items",
        sa.Column("search_run_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("source_domain", sa.String(253), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("facts", JSONB, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "freshness",
            enum("intelligence_freshness", "current", "stale", "unavailable", "deleted"),
            nullable=False,
        ),
        sa.Column(
            "review_status",
            enum("intelligence_review_status", "pending", "accepted", "rejected", "superseded"),
            nullable=False,
        ),
        sa.Column("metadata_snapshot", JSONB, nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "fingerprint", name="uq_intelligence_item_fingerprint"),
    )
    indexes("intelligence_items")
    op.create_index(
        "ix_intelligence_items_run_created", "intelligence_items", ["search_run_id", "created_at"]
    )

    op.create_table(
        "intelligence_snapshots",
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("item_ids", JSONB, nullable=False),
        sa.Column("snapshot_data", JSONB, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "fingerprint", name="uq_intelligence_snapshot_fingerprint"
        ),
    )
    indexes("intelligence_snapshots")

    op.create_table(
        "profile_intelligence_proposals",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("proposed_patch", JSONB, nullable=False),
        sa.Column(
            "status",
            enum(
                "profile_intelligence_proposal_status",
                "pending_confirmation",
                "accepted",
                "rejected",
            ),
            nullable=False,
        ),
        sa.Column("decided_by_id", sa.UUID()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.String(1000)),
        *entity_columns(),
        sa.ForeignKeyConstraint(["profile_id"], ["customer_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["intelligence_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes("profile_intelligence_proposals")
    op.create_index(
        "ix_profile_intelligence_profile",
        "profile_intelligence_proposals",
        ["profile_id", "status"],
    )

    op.create_table(
        "tender_documents",
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("customer_profile_id", sa.UUID()),
        sa.Column("source_filename", sa.String(500)),
        sa.Column("source_mime_type", sa.String(128)),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["customer_profile_id"], ["customer_profiles.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes("tender_documents")

    op.create_table(
        "tender_requirements",
        sa.Column("tender_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("source_location", JSONB, nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(["tender_id"], ["tender_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tender_id", "sequence", name="uq_tender_requirement_sequence"),
    )
    indexes("tender_requirements")

    op.create_table(
        "response_matrices",
        sa.Column("tender_id", sa.UUID(), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_snapshot", JSONB, nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(["tender_id"], ["tender_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes("response_matrices")

    op.create_table(
        "response_matrix_items",
        sa.Column("matrix_id", sa.UUID(), nullable=False),
        sa.Column("requirement_id", sa.UUID(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "evidence_status",
            enum(
                "response_evidence_status",
                "supported",
                "partially_supported",
                "missing_evidence",
                "conflicted",
                "stale",
                "pending_confirmation",
            ),
            nullable=False,
        ),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("risks", JSONB, nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("reviewer_id", sa.UUID()),
        sa.Column("review_note", sa.String(1000)),
        *entity_columns(),
        sa.ForeignKeyConstraint(["matrix_id"], ["response_matrices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["tender_requirements.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes("response_matrix_items")
    op.create_index(
        "ix_response_matrix_items_matrix", "response_matrix_items", ["matrix_id", "created_at"]
    )

    op.create_table(
        "rehearsal_sessions",
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("customer_profile_id", sa.UUID(), nullable=False),
        sa.Column("solution_run_id", sa.UUID()),
        sa.Column("research_task_id", sa.UUID()),
        sa.Column("intelligence_snapshot_id", sa.UUID()),
        sa.Column("response_matrix_id", sa.UUID()),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("difficulty", sa.String(32), nullable=False),
        sa.Column("focus_areas", JSONB, nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            enum(
                "rehearsal_status", "draft", "ready", "running", "completed", "failed", "archived"
            ),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("context_snapshot", JSONB, nullable=False),
        sa.Column("current_turn", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        *entity_columns(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["customer_profile_id"], ["customer_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["solution_run_id"], ["solution_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["research_task_id"], ["research_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["intelligence_snapshot_id"], ["intelligence_snapshots.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["response_matrix_id"], ["response_matrices.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes("rehearsal_sessions")
    op.create_index(
        "ix_rehearsal_workspace_status", "rehearsal_sessions", ["workspace_id", "status"]
    )

    op.create_table(
        "rehearsal_turns",
        sa.Column("rehearsal_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("customer_question", sa.Text(), nullable=False),
        sa.Column("employee_answer", sa.Text()),
        sa.Column("evaluation", JSONB),
        *entity_columns(),
        sa.ForeignKeyConstraint(["rehearsal_id"], ["rehearsal_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rehearsal_id", "sequence", name="uq_rehearsal_turn_sequence"),
    )
    indexes("rehearsal_turns")

    op.create_table(
        "rehearsal_reports",
        sa.Column("rehearsal_id", sa.UUID(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("report_data", JSONB, nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(["rehearsal_id"], ["rehearsal_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rehearsal_id", name="uq_rehearsal_report"),
    )
    indexes("rehearsal_reports")


def downgrade() -> None:
    for table in [
        "rehearsal_reports",
        "rehearsal_turns",
        "rehearsal_sessions",
        "response_matrix_items",
        "response_matrices",
        "tender_requirements",
        "tender_documents",
        "profile_intelligence_proposals",
        "intelligence_snapshots",
        "intelligence_items",
        "search_runs",
    ]:
        op.drop_table(table)
