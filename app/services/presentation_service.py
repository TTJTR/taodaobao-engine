import copy
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    ClaimEvidenceLink,
    ClaimRecord,
    EvidenceRecord,
    ExportArtifact,
    ExportStatus,
    HtmlArtifact,
    PresentationInputSnapshot,
    PresentationRun,
    PresentationStatus,
    ReferenceDeck,
    ReferenceDeckStatus,
    SolutionRun,
    Source,
    SourceFreshness,
    SourceStatus,
    StyleProfile,
    StyleProfileStatus,
    TrustAction,
    TrustDecisionRecord,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.schemas.presentations import (
    CreatePresentationRequest,
    CreateReferenceDeckRequest,
    ExportPresentationRequest,
    GenerateStyleProfileRequest,
    RegeneratePresentationRequest,
    UpdatePresentationBlockRequest,
    UpdateStyleProfileRequest,
)


class PresentationService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

    async def create_reference(self, payload: CreateReferenceDeckRequest) -> ReferenceDeck:
        existing = await self.session.scalar(
            select(ReferenceDeck)
            .where(
                ReferenceDeck.workspace_id == self.workspace_id,
                ReferenceDeck.file_hash == payload.file_hash.lower(),
                ReferenceDeck.is_deleted.is_(False),
            )
            .order_by(ReferenceDeck.version.desc())
            .limit(1)
        )
        if existing is not None:
            return existing
        deck = ReferenceDeck(
            workspace_id=self.workspace_id,
            created_by_id=self.user_id,
            file_id=payload.file_id,
            title=payload.title,
            source_url=payload.source_url,
            storage_key=payload.storage_key,
            file_hash=payload.file_hash.lower(),
            version=1,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
            status=ReferenceDeckStatus.UPLOADED,
            security_report={},
        )
        self.session.add(deck)
        await self.session.flush()
        self.session.add(self._task("reference_parse", deck.id))
        await self.session.commit()
        return deck

    async def get_reference(self, deck_id: uuid.UUID) -> ReferenceDeck:
        deck = await self._get(ReferenceDeck, deck_id, "参考稿不存在")
        return deck

    async def generate_style(self, payload: GenerateStyleProfileRequest) -> StyleProfile:
        decks = list(
            (
                await self.session.scalars(
                    select(ReferenceDeck).where(
                        ReferenceDeck.workspace_id == self.workspace_id,
                        ReferenceDeck.id.in_(payload.reference_deck_ids),
                        ReferenceDeck.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        if len(decks) != len(set(payload.reference_deck_ids)):
            raise AppError(ErrorCode.VALIDATION_FAILED, "部分参考稿不存在", status_code=404)
        if any(item.status != ReferenceDeckStatus.PARSED for item in decks):
            raise AppError(ErrorCode.VALIDATION_FAILED, "参考稿尚未安全解析完成", status_code=409)
        profile = StyleProfile(
            workspace_id=self.workspace_id,
            created_by_id=self.user_id,
            name=payload.name,
            version=1,
            reference_versions=[
                {"deck_id": str(item.id), "version": item.version, "file_hash": item.file_hash}
                for item in decks
            ],
            status=StyleProfileStatus.DRAFT,
            visual_json={},
            narrative_json={},
            conflict_notes=[],
        )
        self.session.add(profile)
        await self.session.flush()
        self.session.add(self._task("style_profile", profile.id))
        await self.session.commit()
        return profile

    async def get_style(self, profile_id: uuid.UUID) -> StyleProfile:
        return await self._get(StyleProfile, profile_id, "风格画像不存在")

    async def update_style(
        self, profile_id: uuid.UUID, payload: UpdateStyleProfileRequest
    ) -> StyleProfile:
        profile = await self.get_style(profile_id)
        self._require_version(profile.version, payload.expected_version)
        if profile.status != StyleProfileStatus.DRAFT:
            raise AppError(ErrorCode.VALIDATION_FAILED, "已确认风格不可直接覆盖", status_code=409)
        if payload.visual_json is not None:
            profile.visual_json = payload.visual_json
        if payload.narrative_json is not None:
            profile.narrative_json = payload.narrative_json
        if payload.conflict_resolutions:
            profile.conflict_notes = []
            profile.narrative_json = {
                **profile.narrative_json,
                "conflict_resolutions": payload.conflict_resolutions,
            }
        profile.version += 1
        await self.session.commit()
        return profile

    async def confirm_style(self, profile_id: uuid.UUID, expected_version: int) -> StyleProfile:
        profile = await self.get_style(profile_id)
        self._require_version(profile.version, expected_version)
        if not profile.visual_json or not profile.narrative_json:
            raise AppError(ErrorCode.VALIDATION_FAILED, "风格画像尚未生成完成", status_code=409)
        if profile.conflict_notes:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "仍有风格冲突需要人工确认",
                status_code=409,
                details={"conflicts": profile.conflict_notes},
            )
        profile.status = StyleProfileStatus.CONFIRMED
        profile.confirmed_by_id = self.user_id
        profile.confirmed_at = datetime.now(UTC)
        await self.session.commit()
        return profile

    async def create_presentation(
        self, run_id: uuid.UUID, payload: CreatePresentationRequest
    ) -> PresentationRun:
        decision = await self._latest_trust_decision(run_id)
        if decision is None or decision.action not in {TrustAction.RELEASE, TrustAction.DOWNGRADE}:
            raise AppError(
                ErrorCode.PRESENTATION_UPSTREAM_NOT_RELEASED,
                "上游方案尚未获准发布，不能生成正式演示稿",
                status_code=409,
                details={"trust_action": decision.action.value if decision else "pending"},
            )
        profile = await self.get_style(payload.style_profile_id)
        if profile.status != StyleProfileStatus.CONFIRMED:
            raise AppError(ErrorCode.VALIDATION_FAILED, "风格画像尚未人工确认", status_code=409)
        solution_run = await self._get(SolutionRun, run_id, "方案运行不存在")
        claims = list(
            (
                await self.session.scalars(
                    select(ClaimRecord).where(
                        ClaimRecord.workspace_id == self.workspace_id,
                        ClaimRecord.solution_run_id == run_id,
                        ClaimRecord.candidate_version == solution_run.result_version,
                        ClaimRecord.released.is_(True),
                        ClaimRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        if not claims:
            raise AppError(
                ErrorCode.PRESENTATION_UPSTREAM_NOT_RELEASED,
                "上游方案没有可发布主张",
                status_code=409,
            )
        await self._assert_current_evidence(claims)
        run = PresentationRun(
            workspace_id=self.workspace_id,
            solution_run_id=run_id,
            solution_version=max(item.candidate_version for item in claims),
            style_profile_id=profile.id,
            style_version=profile.version,
            created_by_id=self.user_id,
            status=PresentationStatus.QUEUED,
            trace_id=str(uuid.uuid4()),
            mode=payload.mode,
            audience=payload.audience,
            language=payload.language,
            requested_outputs=payload.output,
            locked_block_ids=[],
            version=1,
            upstream_trust_version=decision.version,
        )
        self.session.add(run)
        await self.session.flush()
        snapshot = await self._build_input_snapshot(run, claims, profile)
        self.session.add(snapshot)
        await self._queue_render(run, {"operation": "initial"})
        await self.session.commit()
        return run

    async def get_presentation(self, presentation_id: uuid.UUID) -> dict:
        run = await self._get(PresentationRun, presentation_id, "演示稿不存在")
        decision = await self._latest_trust_decision(run.solution_run_id)
        if decision is None or decision.action == TrustAction.BLOCK:
            run.status = PresentationStatus.BLOCKED
        elif (
            decision.version != run.upstream_trust_version
            and run.status == PresentationStatus.READY
        ):
            run.status = PresentationStatus.STALE
        claims = await self._released_claims(run.solution_run_id, run.solution_version)
        try:
            await self._assert_current_evidence(claims)
        except AppError as exc:
            run.status = (
                PresentationStatus.BLOCKED
                if exc.code == ErrorCode.EVIDENCE_PERMISSION_REVOKED
                else PresentationStatus.STALE
            )
        artifact = (
            None
            if run.status in {PresentationStatus.BLOCKED, PresentationStatus.STALE}
            else await self._latest_artifact(run.id)
        )
        await self.session.commit()
        return self._serialize_presentation(run, artifact)

    async def update_block(
        self,
        presentation_id: uuid.UUID,
        block_id: str,
        payload: UpdatePresentationBlockRequest,
    ) -> PresentationRun:
        run = await self._get(PresentationRun, presentation_id, "演示稿不存在")
        self._require_version(run.version, payload.expected_version)
        if run.status in {PresentationStatus.BLOCKED, PresentationStatus.STALE}:
            raise AppError(
                ErrorCode.PRESENTATION_UPSTREAM_NOT_RELEASED, "演示稿上游已失效", status_code=409
            )
        artifact = await self._latest_artifact(run.id)
        if artifact is None or run.spec is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "演示稿尚未生成", status_code=409)
        spec = copy.deepcopy(run.spec)
        block = next(
            (
                item
                for page in spec.get("pages", [])
                for item in page.get("blocks", [])
                if item.get("block_id") == block_id
            ),
            None,
        )
        if block is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "演示区块不存在", status_code=404)
        if payload.text is not None:
            block["text"] = payload.text
            released_claim = await self.session.scalar(
                select(ClaimRecord).where(
                    ClaimRecord.workspace_id == self.workspace_id,
                    ClaimRecord.solution_run_id == run.solution_run_id,
                    ClaimRecord.claim_key == (payload.claim_id or block.get("claim_id")),
                    ClaimRecord.released.is_(True),
                    ClaimRecord.is_deleted.is_(False),
                )
            )
            if block.get("fact_block") and (
                released_claim is None or released_claim.claim_text != payload.text
            ):
                block["needs_claim_review"] = True
                run.status = PresentationStatus.NEEDS_REVIEW
        if payload.color is not None:
            block["color"] = payload.color
        if payload.image_key is not None:
            block["image_key"] = payload.image_key
        if payload.position is not None:
            block["position"] = payload.position
        if payload.locked is not None:
            block["locked"] = payload.locked
            locked = set(run.locked_block_ids)
            (locked.add if payload.locked else locked.discard)(block_id)
            run.locked_block_ids = sorted(locked)
        run.spec = spec
        run.version += 1
        await self._queue_render(run, {"spec_override": spec, "operation": "block_edit"})
        await self.session.commit()
        return run

    async def regenerate(
        self, presentation_id: uuid.UUID, payload: RegeneratePresentationRequest
    ) -> PresentationRun:
        run = await self._get(PresentationRun, presentation_id, "演示稿不存在")
        self._require_version(run.version, payload.expected_version)
        if any(block_id in run.locked_block_ids for block_id in payload.block_ids):
            raise AppError(ErrorCode.VALIDATION_FAILED, "不能重生成已锁定区块", status_code=409)
        run.version += 1
        await self._queue_render(
            run,
            {"block_ids": payload.block_ids, "locked_block_ids": run.locked_block_ids},
        )
        await self.session.commit()
        return run

    async def export(
        self, presentation_id: uuid.UUID, payload: ExportPresentationRequest
    ) -> ExportArtifact:
        run = await self._get(PresentationRun, presentation_id, "演示稿不存在")
        self._require_version(run.version, payload.expected_version)
        if run.status != PresentationStatus.READY:
            raise AppError(
                ErrorCode.PRESENTATION_RENDER_FAILED,
                "演示稿尚未通过渲染验收，不能导出正式版本",
                status_code=409,
            )
        decision = await self._latest_trust_decision(run.solution_run_id)
        if decision is None or decision.action not in {TrustAction.RELEASE, TrustAction.DOWNGRADE}:
            run.status = PresentationStatus.BLOCKED
            raise AppError(
                ErrorCode.PRESENTATION_UPSTREAM_NOT_RELEASED,
                "上游可信状态已变化，导出被阻断",
                status_code=409,
            )
        claims = await self._released_claims(run.solution_run_id, run.solution_version)
        try:
            await self._assert_current_evidence(claims)
        except AppError as exc:
            run.status = (
                PresentationStatus.BLOCKED
                if exc.code == ErrorCode.EVIDENCE_PERMISSION_REVOKED
                else PresentationStatus.STALE
            )
            await self.session.commit()
            raise
        artifact = await self._latest_artifact(run.id)
        if artifact is None:
            raise AppError(ErrorCode.PRESENTATION_RENDER_FAILED, "HTML 产物不存在", status_code=409)
        existing = await self.session.scalar(
            select(ExportArtifact).where(
                ExportArtifact.workspace_id == self.workspace_id,
                ExportArtifact.presentation_id == run.id,
                ExportArtifact.html_artifact_id == artifact.id,
                ExportArtifact.export_type == payload.export_type,
                ExportArtifact.is_deleted.is_(False),
            )
        )
        if existing is not None:
            if existing.status == ExportStatus.FAILED:
                existing.status = ExportStatus.QUEUED
                existing.error_code = None
                task = await self.session.scalar(
                    select(WorkflowTask).where(
                        WorkflowTask.workspace_id == self.workspace_id,
                        WorkflowTask.kind == "presentation_export",
                        WorkflowTask.target_id == existing.id,
                        WorkflowTask.is_deleted.is_(False),
                    )
                )
                if task is None:
                    task = self._task("presentation_export", existing.id, trace_id=run.trace_id)
                    self.session.add(task)
                task.status = WorkflowTaskStatus.QUEUED
                task.stage = "queued"
                task.available_at = datetime.now(UTC)
                task.attempt_count = 0
                task.error_code = None
                task.error_summary = None
                task.finished_at = None
                await self.session.commit()
            return existing
        export = ExportArtifact(
            workspace_id=self.workspace_id,
            presentation_id=run.id,
            html_artifact_id=artifact.id,
            export_type=payload.export_type,
            status=ExportStatus.QUEUED,
            provider_mode="pending",
        )
        self.session.add(export)
        await self.session.flush()
        self.session.add(self._task("presentation_export", export.id, trace_id=run.trace_id))
        await self.session.commit()
        return export

    async def _build_input_snapshot(
        self, run: PresentationRun, claims: list[ClaimRecord], profile: StyleProfile
    ) -> PresentationInputSnapshot:
        claim_ids = [item.id for item in claims]
        links = list(
            (
                await self.session.scalars(
                    select(ClaimEvidenceLink).where(
                        ClaimEvidenceLink.workspace_id == self.workspace_id,
                        ClaimEvidenceLink.claim_id.in_(claim_ids),
                        ClaimEvidenceLink.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        evidence_ids = {item.evidence_id for item in links}
        evidence = list(
            (
                await self.session.scalars(
                    select(EvidenceRecord).where(
                        EvidenceRecord.workspace_id == self.workspace_id,
                        EvidenceRecord.id.in_(evidence_ids),
                        EvidenceRecord.permission_status == "granted",
                        EvidenceRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        return PresentationInputSnapshot(
            workspace_id=self.workspace_id,
            presentation_id=run.id,
            version=run.version,
            schema_version="presentation-input-v1",
            snapshot_data={
                "schema_version": "presentation-input-v1",
                "title": "淘到宝可信方案",
                "solution_run_id": str(run.solution_run_id),
                "solution_version": run.solution_version,
                "trust_version": run.upstream_trust_version,
                "style_profile_id": str(profile.id),
                "style_version": profile.version,
                "audience": run.audience,
                "language": run.language,
                "released_claims": [
                    {
                        "claim_id": item.claim_key,
                        "text": item.claim_text,
                        "boundary": item.boundary,
                        "risk_level": item.risk_level,
                    }
                    for item in claims
                ],
                "evidence": [
                    {
                        "evidence_id": str(item.id),
                        "source_id": str(item.source_id),
                        "source_version": item.source_version,
                        "quote": item.quote,
                        "location": item.location,
                    }
                    for item in evidence
                ],
            },
        )

    async def _released_claims(
        self, run_id: uuid.UUID, candidate_version: int
    ) -> list[ClaimRecord]:
        return list(
            (
                await self.session.scalars(
                    select(ClaimRecord).where(
                        ClaimRecord.workspace_id == self.workspace_id,
                        ClaimRecord.solution_run_id == run_id,
                        ClaimRecord.candidate_version == candidate_version,
                        ClaimRecord.released.is_(True),
                        ClaimRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )

    async def _assert_current_evidence(self, claims: list[ClaimRecord]) -> None:
        required_claim_ids = {
            item.id
            for item in claims
            if item.boundary in {"historical_fact", "enterprise_capability"}
        }
        if not required_claim_ids:
            return
        links = list(
            (
                await self.session.scalars(
                    select(ClaimEvidenceLink).where(
                        ClaimEvidenceLink.workspace_id == self.workspace_id,
                        ClaimEvidenceLink.claim_id.in_(required_claim_ids),
                        ClaimEvidenceLink.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        linked_claim_ids = {item.claim_id for item in links}
        if not required_claim_ids.issubset(linked_claim_ids):
            raise AppError(
                ErrorCode.PRESENTATION_UPSTREAM_NOT_RELEASED,
                "正式主张缺少可发布证据，不能生成或导出演示稿",
                status_code=409,
            )
        evidence_ids = {item.evidence_id for item in links}
        evidence = list(
            (
                await self.session.scalars(
                    select(EvidenceRecord).where(
                        EvidenceRecord.workspace_id == self.workspace_id,
                        EvidenceRecord.id.in_(evidence_ids),
                        EvidenceRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        if {item.id for item in evidence} != evidence_ids:
            raise AppError(
                ErrorCode.EVIDENCE_PERMISSION_REVOKED,
                "演示稿证据记录已失效",
                status_code=409,
            )
        sources = {
            item.id: item
            for item in (
                await self.session.scalars(
                    select(Source).where(
                        Source.workspace_id == self.workspace_id,
                        Source.id.in_({item.source_id for item in evidence}),
                        Source.is_deleted.is_(False),
                    )
                )
            ).all()
        }
        for item in evidence:
            source = sources.get(item.source_id)
            if (
                source is None
                or item.permission_status != "granted"
                or source.status != SourceStatus.COMPLETED
                or source.freshness_status
                in {SourceFreshness.PERMISSION_DENIED, SourceFreshness.DELETED}
            ):
                raise AppError(
                    ErrorCode.EVIDENCE_PERMISSION_REVOKED,
                    "演示稿证据来源权限已失效",
                    status_code=409,
                )
            if (
                source.freshness_status != SourceFreshness.CURRENT
                or source.content_version != item.source_version
                or item.reviewed_source_version != source.content_version
            ):
                raise AppError(
                    ErrorCode.EVIDENCE_VERSION_STALE,
                    "演示稿证据来源版本已变化",
                    status_code=409,
                )

    async def _queue_render(self, run: PresentationRun, payload: dict) -> None:
        task = await self.session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.workspace_id == self.workspace_id,
                WorkflowTask.kind == "presentation_render",
                WorkflowTask.target_id == run.id,
                WorkflowTask.is_deleted.is_(False),
            )
        )
        if task is None:
            task = self._task("presentation_render", run.id, trace_id=run.trace_id)
            self.session.add(task)
        task.status = WorkflowTaskStatus.QUEUED
        task.stage = "queued"
        task.payload = payload
        task.available_at = datetime.now(UTC)
        task.attempt_count = 0
        task.finished_at = None
        task.error_code = None
        run.status = PresentationStatus.QUEUED

    async def _latest_artifact(self, presentation_id: uuid.UUID) -> HtmlArtifact | None:
        return await self.session.scalar(
            select(HtmlArtifact)
            .where(
                HtmlArtifact.workspace_id == self.workspace_id,
                HtmlArtifact.presentation_id == presentation_id,
                HtmlArtifact.is_deleted.is_(False),
            )
            .order_by(HtmlArtifact.version.desc())
            .limit(1)
        )

    async def _latest_trust_decision(self, run_id: uuid.UUID) -> TrustDecisionRecord | None:
        return await self.session.scalar(
            select(TrustDecisionRecord)
            .where(
                TrustDecisionRecord.workspace_id == self.workspace_id,
                TrustDecisionRecord.solution_run_id == run_id,
                TrustDecisionRecord.is_deleted.is_(False),
            )
            .order_by(TrustDecisionRecord.version.desc())
            .limit(1)
        )

    async def _get(self, model, entity_id: uuid.UUID, message: str):
        item = await self.session.scalar(
            select(model).where(
                model.id == entity_id,
                model.workspace_id == self.workspace_id,
                model.is_deleted.is_(False),
            )
        )
        if item is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, message, status_code=404)
        return item

    def _task(self, kind: str, target_id: uuid.UUID, *, trace_id: str | None = None):
        return WorkflowTask(
            workspace_id=self.workspace_id,
            kind=kind,
            target_id=target_id,
            status=WorkflowTaskStatus.QUEUED,
            stage="queued",
            trace_id=trace_id or str(uuid.uuid4()),
            payload={},
            attempt_count=0,
            max_attempts=3,
            available_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=120),
        )

    @staticmethod
    def _require_version(current: int, expected: int) -> None:
        if current != expected:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "资源版本已变化，请刷新后重试",
                status_code=409,
                details={"current_version": current},
            )

    @staticmethod
    def _serialize_presentation(run: PresentationRun, artifact: HtmlArtifact | None) -> dict:
        return {
            "id": str(run.id),
            "solution_run_id": str(run.solution_run_id),
            "solution_version": run.solution_version,
            "style_profile_id": str(run.style_profile_id),
            "style_version": run.style_version,
            "status": run.status.value,
            "trace_id": run.trace_id,
            "mode": run.mode,
            "audience": run.audience,
            "language": run.language,
            "requested_outputs": run.requested_outputs,
            "spec": run.spec,
            "locked_block_ids": run.locked_block_ids,
            "version": run.version,
            "upstream_trust_version": run.upstream_trust_version,
            "error_code": run.error_code,
            "artifact": (
                {
                    "artifact_id": str(artifact.id),
                    "version": artifact.version,
                    "html": artifact.html,
                    "css": artifact.css,
                    "assets": artifact.assets,
                    "render_report": artifact.render_report,
                    "provider_mode": artifact.provider_mode,
                }
                if artifact
                else None
            ),
        }
