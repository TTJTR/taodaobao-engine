"""Freeze explicitly selected V2 intelligence context without reclassifying it as evidence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import IntelligenceSnapshot, ResponseMatrix, TenderDocument


class ExternalContextService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def freeze(
        self,
        *,
        profile_id: uuid.UUID,
        intelligence_snapshot_id: uuid.UUID | None,
        response_matrix_id: uuid.UUID | None,
    ) -> dict[str, Any] | None:
        intelligence = None
        matrix = None
        if intelligence_snapshot_id is not None:
            intelligence = await self._get(IntelligenceSnapshot, intelligence_snapshot_id)
            item_profiles = {
                item.get("metadata_snapshot", {}).get("profile_id")
                for item in intelligence.snapshot_data.get("items", [])
            }
            if item_profiles != {str(profile_id)}:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED, "情报快照与客户画像不匹配", status_code=409
                )
        if response_matrix_id is not None:
            matrix = await self._get(ResponseMatrix, response_matrix_id)
            tender = await self._get(TenderDocument, matrix.tender_id)
            if tender.customer_profile_id != profile_id:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED, "响应矩阵与客户画像不匹配", status_code=409
                )
        if intelligence is None and matrix is None:
            return None
        return {
            "captured_at": datetime.now(UTC).isoformat(),
            "boundary": (
                "external intelligence is context only and cannot prove enterprise capability"
            ),
            "intelligence_snapshot": {
                "id": str(intelligence.id),
                "snapshot_data": intelligence.snapshot_data,
            } if intelligence else None,
            "response_matrix": {
                "id": str(matrix.id),
                "evidence_snapshot": matrix.evidence_snapshot,
            } if matrix else None,
        }

    async def _get(self, model, entity_id: uuid.UUID):
        entity = await self.session.scalar(
            select(model).where(
                model.id == entity_id,
                model.workspace_id == self.workspace_id,
                model.is_deleted.is_(False),
            )
        )
        if entity is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
        return entity
