"""add response matrix trust fields

Revision ID: f73b2d5c10e4
Revises: e62a1c4b09d3
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f73b2d5c10e4"
down_revision: str | Sequence[str] | None = "e62a1c4b09d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("response_matrix_items", sa.Column("ai_draft", sa.Text()))
    op.add_column("response_matrix_items", sa.Column("current_answer", sa.Text()))
    for name in (
        "internal_exp_links",
        "internal_cap_links",
        "external_ctx_links",
        "risk_flags",
    ):
        op.add_column(
            "response_matrix_items",
            sa.Column(name, JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        )
    op.add_column(
        "response_matrix_items",
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "response_matrix_items",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.execute(
        "UPDATE response_matrix_items SET ai_draft=response_text, current_answer=response_text"
    )
    op.execute(
        """
        UPDATE response_matrix_items
        SET risk_flags = CASE
          WHEN evidence_status = 'missing_evidence' THEN '["missing_evidence"]'::jsonb
          ELSE '[]'::jsonb
        END,
        review_status = CASE
          WHEN review_status = 'accepted' THEN 'approved'
          WHEN review_status = 'needs_revision' THEN 'needs_evidence'
          ELSE review_status
        END
        """
    )
    op.alter_column("response_matrix_items", "ai_draft", nullable=False)
    op.alter_column("response_matrix_items", "current_answer", nullable=False)


def downgrade() -> None:
    op.execute(
        """
        UPDATE response_matrix_items
        SET response_text=current_answer,
            risks=CASE
              WHEN risk_flags ? 'missing_evidence'
              THEN '["缺少企业内部依据，需要补充材料或人工确认"]'::jsonb
              ELSE risks
            END,
            review_status=CASE
              WHEN review_status='approved' THEN 'accepted'
              WHEN review_status='needs_evidence' THEN 'needs_revision'
              ELSE review_status
            END
        """
    )
    op.drop_column("response_matrix_items", "version")
    op.drop_column("response_matrix_items", "approved_at")
    for name in reversed(
        ("internal_exp_links", "internal_cap_links", "external_ctx_links", "risk_flags")
    ):
        op.drop_column("response_matrix_items", name)
    op.drop_column("response_matrix_items", "current_answer")
    op.drop_column("response_matrix_items", "ai_draft")
