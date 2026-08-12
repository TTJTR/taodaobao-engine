"""add raw artifacts and tender document locations

Revision ID: c41d9e2f7a10
Revises: 8b11c0d2a001
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c41d9e2f7a10"
down_revision: str | Sequence[str] | None = "8b11c0d2a001"
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
        "raw_artifacts",
        sa.Column("search_run_id", sa.UUID()),
        sa.Column("artifact_key", sa.String(64), nullable=False),
        sa.Column(
            "kind",
            enum("raw_artifact_kind", "web_page", "tender_file", "pasted_text"),
            nullable=False,
        ),
        sa.Column(
            "status",
            enum("raw_artifact_status", "captured", "validated", "rejected", "unavailable"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("normalized_url", sa.String(2000)),
        sa.Column("source_filename", sa.String(500)),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_uri", sa.String(2000)),
        sa.Column("text_content", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_report", JSONB, nullable=False),
        sa.Column("metadata_snapshot", JSONB, nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(1000)),
        *entity_columns(),
        sa.ForeignKeyConstraint(
            ["search_run_id"],
            ["search_runs.id"],
            name="fk_raw_artifacts_search_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "artifact_key", name="uq_raw_artifact_key"),
    )
    indexes("raw_artifacts")
    op.create_index(
        "ix_raw_artifacts_content_sha256", "raw_artifacts", ["workspace_id", "content_sha256"]
    )
    op.create_index(
        "ix_raw_artifacts_run_captured", "raw_artifacts", ["search_run_id", "captured_at"]
    )

    op.create_table(
        "intelligence_item_artifact_links",
        sa.Column("intelligence_item_id", sa.UUID(), nullable=False),
        sa.Column("raw_artifact_id", sa.UUID(), nullable=False),
        sa.Column(
            "relation_type",
            enum(
                "artifact_relation_type", "primary", "duplicate", "corroborating", "conflicting"
            ),
            nullable=False,
        ),
        sa.Column("source_snapshot", JSONB, nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(
            ["intelligence_item_id"],
            ["intelligence_items.id"],
            name="fk_intelligence_artifact_links_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id"],
            ["raw_artifacts.id"],
            name="fk_intelligence_artifact_links_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "intelligence_item_id",
            "raw_artifact_id",
            name="uq_intelligence_item_artifact_link",
        ),
    )
    indexes("intelligence_item_artifact_links")
    op.create_index(
        "ix_intelligence_item_artifacts_item",
        "intelligence_item_artifact_links",
        ["intelligence_item_id", "created_at"],
    )

    op.add_column("tender_documents", sa.Column("raw_artifact_id", sa.UUID()))
    op.create_foreign_key(
        "fk_tender_documents_raw_artifact",
        "tender_documents",
        "raw_artifacts",
        ["raw_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "tender_parse_versions",
        sa.Column("tender_id", sa.UUID(), nullable=False),
        sa.Column("raw_artifact_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parser_name", sa.String(128), nullable=False),
        sa.Column("parser_version", sa.String(128), nullable=False),
        sa.Column("document_schema_version", sa.String(32), nullable=False),
        sa.Column(
            "status",
            enum("tender_parse_status", "queued", "parsing", "parsed", "failed", "rejected"),
            nullable=False,
        ),
        sa.Column("document_ir", JSONB),
        sa.Column("resource_usage", JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(1000)),
        *entity_columns(),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tender_documents.id"],
            name="fk_tender_parse_versions_tender",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id"],
            ["raw_artifacts.id"],
            name="fk_tender_parse_versions_artifact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tender_id", "version", name="uq_tender_parse_version"),
    )
    indexes("tender_parse_versions")
    op.create_index(
        "ix_tender_parse_versions_tender", "tender_parse_versions", ["tender_id", "version"]
    )

    op.add_column("tender_requirements", sa.Column("parse_version_id", sa.UUID()))
    op.add_column("tender_requirements", sa.Column("version", sa.Integer()))
    op.add_column("tender_requirements", sa.Column("acceptance_condition", sa.Text()))
    op.add_column("tender_requirements", sa.Column("constraints", JSONB))
    op.add_column("tender_requirements", sa.Column("ambiguities", JSONB))
    op.add_column(
        "tender_requirements",
        sa.Column(
            "status",
            enum(
                "tender_requirement_status",
                "ai_draft",
                "edited",
                "confirmed",
                "rejected",
                "superseded",
            ),
        ),
    )
    op.add_column("tender_requirements", sa.Column("confirmed_by_id", sa.UUID()))
    op.add_column("tender_requirements", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.execute(
        """
        UPDATE tender_requirements
        SET version = 1,
            constraints = '{}'::jsonb,
            ambiguities = '[]'::jsonb,
            status = 'ai_draft'
        """
    )
    op.execute(
        """
        UPDATE tender_requirements AS requirement
        SET source_location = jsonb_build_object(
            'kind', 'plain_text',
            'schema_version', 'document-location-v1',
            'quote_hash', encode(
                sha256(convert_to(requirement.requirement_text, 'UTF8')),
                'hex'
            ),
            'start_offset', GREATEST(
                strpos(document.content_text, requirement.requirement_text) - 1,
                0
            ),
            'end_offset', GREATEST(
                strpos(document.content_text, requirement.requirement_text) - 1,
                0
            ) + char_length(requirement.requirement_text)
        )
        FROM tender_documents AS document
        WHERE document.id = requirement.tender_id
        """
    )
    for column in ["version", "constraints", "ambiguities", "status"]:
        op.alter_column("tender_requirements", column, nullable=False)
    op.create_foreign_key(
        "fk_tender_requirements_parse_version",
        "tender_requirements",
        "tender_parse_versions",
        ["parse_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_tender_requirements_confirmed_by",
        "tender_requirements",
        "users",
        ["confirmed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "tender_requirement_versions",
        sa.Column("requirement_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by_id", sa.UUID()),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("requirement_snapshot", JSONB, nullable=False),
        sa.Column("source_location", JSONB, nullable=False),
        *entity_columns(),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["tender_requirements.id"],
            name="fk_tender_requirement_versions_requirement",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"],
            ["users.id"],
            name="fk_tender_requirement_versions_changed_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("requirement_id", "version", name="uq_tender_requirement_version"),
    )
    indexes("tender_requirement_versions")
    op.create_index(
        "ix_tender_requirement_versions_requirement",
        "tender_requirement_versions",
        ["requirement_id", "version"],
    )
    op.execute(
        """
        INSERT INTO tender_requirement_versions (
            id, created_at, updated_at, is_deleted, workspace_id,
            requirement_id, version, changed_by_id, change_type,
            requirement_snapshot, source_location
        )
        SELECT
            gen_random_uuid(), created_at, updated_at, false, workspace_id,
            id, 1, NULL, 'migrated',
            jsonb_build_object(
                'requirement_text', requirement_text,
                'category', category,
                'mandatory', mandatory,
                'acceptance_condition', acceptance_condition,
                'constraints', constraints,
                'ambiguities', ambiguities,
                'status', status
            ),
            source_location
        FROM tender_requirements
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tender_requirement_versions_requirement", table_name="tender_requirement_versions"
    )
    op.drop_table("tender_requirement_versions")

    op.drop_constraint(
        "fk_tender_requirements_confirmed_by", "tender_requirements", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_tender_requirements_parse_version", "tender_requirements", type_="foreignkey"
    )
    for column in [
        "confirmed_at",
        "confirmed_by_id",
        "status",
        "ambiguities",
        "constraints",
        "acceptance_condition",
        "version",
        "parse_version_id",
    ]:
        op.drop_column("tender_requirements", column)

    op.drop_index("ix_tender_parse_versions_tender", table_name="tender_parse_versions")
    op.drop_table("tender_parse_versions")

    op.drop_constraint(
        "fk_tender_documents_raw_artifact", "tender_documents", type_="foreignkey"
    )
    op.drop_column("tender_documents", "raw_artifact_id")

    op.drop_index(
        "ix_intelligence_item_artifacts_item", table_name="intelligence_item_artifact_links"
    )
    op.drop_table("intelligence_item_artifact_links")

    op.drop_index("ix_raw_artifacts_run_captured", table_name="raw_artifacts")
    op.drop_index("ix_raw_artifacts_content_sha256", table_name="raw_artifacts")
    op.drop_table("raw_artifacts")
