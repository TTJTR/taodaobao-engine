"""init core models

Revision ID: 554fd08dde0a
Revises:
Create Date: 2026-08-06 14:55:01.535409
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "554fd08dde0a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def _entity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]


def _create_common_indexes(table_name: str) -> None:
    op.create_index(f"ix_{table_name}_is_deleted", table_name, ["is_deleted"])
    op.create_index(f"ix_{table_name}_workspace_id", table_name, ["workspace_id"])


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("feishu_user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("avatar", sa.String(length=1000), nullable=True),
        *_entity_columns(),
        sa.UniqueConstraint(
            "workspace_id", "feishu_user_id", name="uq_users_workspace_feishu"
        ),
    )
    _create_common_indexes("users")

    op.create_table(
        "customer_profiles",
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            _enum("profile_status", "pending_confirmation", "confirmed"),
            nullable=False,
        ),
        *_entity_columns(),
        sa.UniqueConstraint(
            "workspace_id", "customer_name", name="uq_customer_profiles_workspace_name"
        ),
    )
    _create_common_indexes("customer_profiles")

    op.create_table(
        "jobs",
        sa.Column(
            "type",
            _enum("job_type", "source_processing", "profile_generation"),
            nullable=False,
        ),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            _enum("job_status", "pending", "running", "completed", "failed"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_entity_columns(),
    )
    _create_common_indexes("jobs")
    op.create_index("ix_jobs_target", "jobs", ["target_id", "type"])
    op.create_index("ix_jobs_workspace_status", "jobs", ["workspace_id", "status"])

    op.create_table(
        "sources",
        sa.Column("customer_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "type",
            _enum("source_type", "feishu_doc", "pasted_text"),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            _enum("source_purpose", "customer_profile", "experience", "capability"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            _enum(
                "source_status",
                "pending",
                "fetching",
                "parsing",
                "extracting",
                "pending_review",
                "completed",
                "failed",
            ),
            nullable=False,
        ),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["customer_profile_id"], ["customer_profiles.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["imported_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("workspace_id", "source_url", name="uq_sources_workspace_url"),
    )
    _create_common_indexes("sources")
    op.create_index("ix_sources_customer_profile_id", "sources", ["customer_profile_id"])
    op.create_index("ix_sources_imported_by_id", "sources", ["imported_by_id"])
    op.create_index("ix_sources_workspace_status", "sources", ["workspace_id", "status"])

    op.create_table(
        "experiences",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "review_status",
            _enum(
                "experience_review_status",
                "pending_review",
                "verified",
                "rejected",
                "source_updated",
            ),
            nullable=False,
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        *_entity_columns(),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_id", name="uq_experiences_source"),
    )
    _create_common_indexes("experiences")
    op.create_index(
        "ix_experiences_workspace_review", "experiences", ["workspace_id", "review_status"]
    )

    op.create_table(
        "capabilities",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "review_status",
            _enum(
                "capability_review_status",
                "pending_review",
                "verified",
                "rejected",
                "source_updated",
            ),
            nullable=False,
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        *_entity_columns(),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
    )
    _create_common_indexes("capabilities")
    op.create_index(
        "ix_capabilities_workspace_review",
        "capabilities",
        ["workspace_id", "review_status"],
    )

    op.create_table(
        "sessions",
        sa.Column("customer_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["customer_profile_id"], ["customer_profiles.id"], ondelete="RESTRICT"
        ),
    )
    _create_common_indexes("sessions")
    op.create_index(
        "ix_sessions_profile_updated", "sessions", ["customer_profile_id", "updated_at"]
    )

    op.create_table(
        "messages",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("solution_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", _enum("message_role", "user", "assistant"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        *_entity_columns(),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_messages_session_sequence"),
    )
    _create_common_indexes("messages")
    op.create_index("ix_messages_solution_run_id", "messages", ["solution_run_id"])

    op.create_table(
        "solution_runs",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retrieval_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("display_text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum("solution_run_status", "pending", "running", "completed", "failed"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["request_message_id"], ["messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="RESTRICT"),
    )
    _create_common_indexes("solution_runs")
    op.create_index(
        "ix_solution_runs_session_created", "solution_runs", ["session_id", "created_at"]
    )
    op.create_foreign_key(
        "fk_messages_solution_run",
        "messages",
        "solution_runs",
        ["solution_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_messages_solution_run", "messages", type_="foreignkey")
    op.drop_table("solution_runs")
    op.drop_table("messages")
    op.drop_table("capabilities")
    op.drop_table("experiences")
    op.drop_table("sources")
    op.drop_table("sessions")
    op.drop_table("jobs")
    op.drop_table("customer_profiles")
    op.drop_table("users")
