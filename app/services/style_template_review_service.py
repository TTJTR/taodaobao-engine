import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    StyleProfile,
    StyleProfileStatus,
    StyleTemplateStatus,
    StyleTemplateVersion,
)
from app.presentation.style.legacy_adapter import adapt_legacy_style_profile
from app.presentation.style.template_compiler import TemplateCompiler
from app.presentation.style.template_preview import build_sanitized_preview
from app.schemas.presentations import (
    ConfirmTemplateCandidateRequest,
    RegenerateTemplateCandidatesRequest,
)
from app.schemas.style_profile import VisualStyleProfileV2


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class StyleTemplateReviewService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

    async def list_candidates(
        self, profile_id: uuid.UUID
    ) -> tuple[StyleProfile, list[StyleTemplateVersion]]:
        profile = await self._profile(profile_id)
        latest_version = await self.session.scalar(
            select(func.max(StyleTemplateVersion.version)).where(
                StyleTemplateVersion.workspace_id == self.workspace_id,
                StyleTemplateVersion.style_profile_id == profile_id,
                StyleTemplateVersion.is_deleted.is_(False),
            )
        )
        if latest_version is None:
            return profile, []
        rows = list(
            (
                await self.session.scalars(
                    select(StyleTemplateVersion)
                    .where(
                        StyleTemplateVersion.workspace_id == self.workspace_id,
                        StyleTemplateVersion.style_profile_id == profile_id,
                        StyleTemplateVersion.version == latest_version,
                        StyleTemplateVersion.is_deleted.is_(False),
                    )
                    .order_by(
                        StyleTemplateVersion.status.asc(),
                        StyleTemplateVersion.archetype_token.asc(),
                    )
                )
            ).all()
        )
        return profile, rows

    async def regenerate(
        self,
        profile_id: uuid.UUID,
        payload: RegenerateTemplateCandidatesRequest,
    ) -> tuple[StyleProfile, list[StyleTemplateVersion]]:
        profile = await self._profile(profile_id, for_update=True)
        self._require_version(profile.version, payload.expected_profile_version)
        if profile.status != StyleProfileStatus.DRAFT:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "已确认风格画像不可重新生成候选模板",
                status_code=409,
            )
        style = self._style_profile(profile)
        bundle = TemplateCompiler().compile(style)
        next_version = int(
            await self.session.scalar(
                select(func.coalesce(func.max(StyleTemplateVersion.version), 0)).where(
                    StyleTemplateVersion.workspace_id == self.workspace_id,
                    StyleTemplateVersion.style_profile_id == profile_id,
                    StyleTemplateVersion.is_deleted.is_(False),
                )
            )
            or 0
        ) + 1
        await self._supersede_open_candidates(profile_id)
        source_hashes = sorted(
            str(item.get("file_hash"))
            for item in profile.reference_versions
            if item.get("file_hash")
        )
        feature_hash = str(
            profile.visual_json.get("feature_set_hash")
            or _canonical_hash(style.model_dump(mode="json"))
        )
        rows = []
        for candidate in bundle.candidates:
            preview = build_sanitized_preview(candidate, style)
            row = StyleTemplateVersion(
                workspace_id=self.workspace_id,
                style_profile_id=profile.id,
                candidate_id=candidate.candidate_id,
                version=next_version,
                status=StyleTemplateStatus(candidate.status),
                archetype_token=candidate.archetype_token,
                source_deck_hashes=source_hashes,
                feature_set_hash=feature_hash,
                compiled_template_hash=_canonical_hash(
                    candidate.layout_template.model_dump(mode="json")
                ),
                compiler_version=bundle.compiler_version,
                compiled_template_json=candidate.layout_template.model_dump(mode="json"),
                confidence_report={
                    "candidate": candidate.confidence,
                    "profile": style.confidence_report.model_dump(mode="json"),
                },
                validation_report=candidate.validation.model_dump(mode="json"),
                preview_artifacts=preview,
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        profile.visual_json = {
            **profile.visual_json,
            "template_generation": next_version,
            "template_candidate_ids": [str(row.candidate_id) for row in rows],
        }
        profile.version += 1
        await self.session.commit()
        return profile, rows

    async def confirm(
        self,
        profile_id: uuid.UUID,
        candidate_id: uuid.UUID,
        payload: ConfirmTemplateCandidateRequest,
    ) -> tuple[StyleProfile, StyleTemplateVersion]:
        profile = await self._profile(profile_id, for_update=True)
        self._require_version(profile.version, payload.expected_profile_version)
        candidate = await self.session.scalar(
            select(StyleTemplateVersion)
            .where(
                StyleTemplateVersion.workspace_id == self.workspace_id,
                StyleTemplateVersion.style_profile_id == profile_id,
                StyleTemplateVersion.candidate_id == candidate_id,
                StyleTemplateVersion.version == payload.expected_candidate_version,
                StyleTemplateVersion.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if candidate is None:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "候选模板不存在或版本已变化",
                status_code=404,
            )
        if candidate.status != StyleTemplateStatus.NEEDS_REVIEW:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "候选模板当前不可确认",
                status_code=409,
            )
        if not candidate.validation_report.get("passed"):
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "候选模板未通过容量与结构校验",
                status_code=409,
            )
        peers = list(
            (
                await self.session.scalars(
                    select(StyleTemplateVersion)
                    .where(
                        StyleTemplateVersion.workspace_id == self.workspace_id,
                        StyleTemplateVersion.style_profile_id == profile_id,
                        StyleTemplateVersion.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
            ).all()
        )
        now = datetime.now(UTC)
        for row in peers:
            if row.id == candidate.id:
                row.status = StyleTemplateStatus.CONFIRMED
                row.confirmed_by_id = self.user_id
                row.confirmed_at = now
            elif row.status == StyleTemplateStatus.CONFIRMED:
                row.status = StyleTemplateStatus.SUPERSEDED
            elif (
                row.version == candidate.version
                and row.status == StyleTemplateStatus.NEEDS_REVIEW
            ):
                row.status = StyleTemplateStatus.REJECTED
        profile.visual_json = {
            **profile.visual_json,
            "confirmed_template": {
                "candidate_id": str(candidate.candidate_id),
                "version": candidate.version,
                "compiled_template_hash": candidate.compiled_template_hash,
            },
        }
        profile.version += 1
        await self.session.commit()
        return profile, candidate

    async def _profile(
        self, profile_id: uuid.UUID, *, for_update: bool = False
    ) -> StyleProfile:
        statement = select(StyleProfile).where(
            StyleProfile.id == profile_id,
            StyleProfile.workspace_id == self.workspace_id,
            StyleProfile.is_deleted.is_(False),
        )
        if for_update:
            statement = statement.with_for_update()
        profile = await self.session.scalar(statement)
        if profile is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "风格画像不存在", status_code=404)
        return profile

    @staticmethod
    def _style_profile(profile: StyleProfile) -> VisualStyleProfileV2:
        payload = profile.visual_json.get("style_profile_v2")
        if payload is None and profile.visual_json.get("schema_version") != "style-profile-v2":
            return adapt_legacy_style_profile(profile.visual_json, profile_id=profile.id)
        payload = payload or profile.visual_json
        try:
            style = VisualStyleProfileV2.model_validate(payload)
        except ValidationError as exc:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "风格画像尚未生成有效的 V1.1 StyleProfile Schema v2",
                status_code=409,
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        if style.profile_id != profile.id:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "StyleProfile Schema v2 与数据库画像 ID 不一致",
                status_code=409,
            )
        return style

    async def _supersede_open_candidates(self, profile_id: uuid.UUID) -> None:
        rows = list(
            (
                await self.session.scalars(
                    select(StyleTemplateVersion)
                    .where(
                        StyleTemplateVersion.workspace_id == self.workspace_id,
                        StyleTemplateVersion.style_profile_id == profile_id,
                        StyleTemplateVersion.status.in_(
                            [
                                StyleTemplateStatus.DRAFT,
                                StyleTemplateStatus.PREVIEWING,
                                StyleTemplateStatus.NEEDS_REVIEW,
                            ]
                        ),
                        StyleTemplateVersion.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for row in rows:
            row.status = StyleTemplateStatus.SUPERSEDED

    @staticmethod
    def _require_version(actual: int, expected: int) -> None:
        if actual != expected:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "版本已变化，请刷新后重试",
                status_code=409,
                details={"expected_version": expected, "actual_version": actual},
            )
