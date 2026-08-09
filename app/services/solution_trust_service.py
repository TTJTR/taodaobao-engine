import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    ClaimEvidenceLink,
    ClaimRecord,
    EvidenceRecord,
    HumanReviewRecord,
    PresentationRun,
    PresentationStatus,
    ProcessStatus,
    SolutionRun,
    Source,
    SourceFreshness,
    SourceStatus,
    TrustAction,
    TrustDecisionRecord,
    VerificationLabel,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.schemas.trust import TrustReviewRequest
from app.services.trust_gate import GATE_POLICY_VERSION, THRESHOLD_VERSION


class SolutionTrustService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

    async def get_run(self, run_id: uuid.UUID) -> SolutionRun:
        run = await self.session.scalar(
            select(SolutionRun).where(
                SolutionRun.id == run_id,
                SolutionRun.workspace_id == self.workspace_id,
                SolutionRun.is_deleted.is_(False),
            )
        )
        if run is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "方案运行不存在", status_code=404)
        return run

    async def latest_decision(self, run_id: uuid.UUID) -> TrustDecisionRecord | None:
        return await self.session.scalar(
            select(TrustDecisionRecord)
            .where(
                TrustDecisionRecord.solution_run_id == run_id,
                TrustDecisionRecord.workspace_id == self.workspace_id,
                TrustDecisionRecord.is_deleted.is_(False),
            )
            .order_by(TrustDecisionRecord.version.desc())
            .limit(1)
        )

    async def summary(self, run_id: uuid.UUID) -> tuple[dict | None, dict]:
        decision = await self.latest_decision(run_id)
        claims = await self._latest_claims(run_id)
        return (
            self._decision_data(decision) if decision else None,
            {
                "total": len(claims),
                "released": sum(item.released for item in claims),
                "needs_review": sum(
                    item.verification_status != VerificationLabel.ENTAILED
                    and item.boundary != "pending_confirmation"
                    for item in claims
                ),
            },
        )

    async def trust_report(self, run_id: uuid.UUID) -> dict:
        run = await self.get_run(run_id)
        decision = await self.latest_decision(run_id)
        claims = await self._latest_claims(run_id)
        evidence = list(
            (
                await self.session.scalars(
                    select(EvidenceRecord).where(
                        EvidenceRecord.solution_run_id == run_id,
                        EvidenceRecord.workspace_id == self.workspace_id,
                        EvidenceRecord.candidate_version == run.result_version,
                        EvidenceRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        source_ids = {item.source_id for item in evidence}
        current_sources = {
            item.id: item
            for item in (
                await self.session.scalars(
                    select(Source).where(
                        Source.workspace_id == self.workspace_id,
                        Source.id.in_(source_ids),
                        Source.is_deleted.is_(False),
                    )
                )
            ).all()
        }
        links = list(
            (
                await self.session.scalars(
                    select(ClaimEvidenceLink).where(
                        ClaimEvidenceLink.solution_run_id == run_id,
                        ClaimEvidenceLink.workspace_id == self.workspace_id,
                        ClaimEvidenceLink.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        claim_keys_by_id = {item.id: item.claim_key for item in claims}
        return {
            "run_id": str(run.id),
            "trace_id": run.trace_id,
            "stage": run.stage,
            "result_version": run.result_version,
            "trust_decision": self._decision_data(decision) if decision else None,
            "claims": [
                {
                    "claim_id": item.claim_key,
                    "section": item.section,
                    "text": item.claim_text,
                    "boundary": item.boundary,
                    "risk_level": item.risk_level,
                    "verification_status": item.verification_status.value,
                    "released": item.released,
                }
                for item in claims
            ],
            "evidence": [
                {
                    "evidence_id": str(item.id),
                    "evidence_key": item.evidence_key,
                    "asset_id": str(item.asset_id),
                    "source_id": str(item.source_id),
                    "source_version": item.source_version,
                    "quote": item.quote if _evidence_still_visible(item, current_sources) else None,
                    "location": (
                        item.location if _evidence_still_visible(item, current_sources) else None
                    ),
                    "permission_status": (
                        "granted" if _evidence_still_visible(item, current_sources) else "revoked"
                    ),
                }
                for item in evidence
            ],
            "claim_evidence_links": [
                {
                    "claim_id": claim_keys_by_id.get(item.claim_id, str(item.claim_id)),
                    "evidence_id": str(item.evidence_id),
                    "label": item.label.value,
                    "score": item.score,
                    "verifier_version": item.verifier_version,
                }
                for item in links
            ],
        }

    async def review(self, run_id: uuid.UUID, payload: TrustReviewRequest) -> dict:
        run = await self.get_run(run_id)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.workspace_id}:trust-review:{run_id}"},
        )
        current = await self.latest_decision(run_id)
        if current is None or current.version != payload.expected_decision_version:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "可信决策版本已变化，请刷新后重试",
                status_code=409,
                details={"current_version": current.version if current else None},
            )
        claims = await self._latest_claims(run_id)
        before = {
            "action": current.action.value,
            "reason_codes": current.reason_codes,
            "released_claim_ids": [item.claim_key for item in claims if item.released],
        }
        delete_ids = set(payload.edits.get("delete_claim_ids") or [])
        downgrade_ids = set(payload.edits.get("downgrade_claim_ids") or [])
        known_ids = {item.claim_key for item in claims}
        if not (delete_ids | downgrade_ids).issubset(known_ids):
            raise AppError(
                ErrorCode.VALIDATION_FAILED, "审核修改包含未知 claim_id", status_code=422
            )

        if payload.decision == "approve":
            unresolved = [
                item
                for item in claims
                if item.risk_level == "high"
                and item.verification_status != VerificationLabel.ENTAILED
                and item.claim_key not in delete_ids | downgrade_ids
            ]
            if unresolved:
                raise AppError(
                    ErrorCode.TRUST_REVIEW_REQUIRED,
                    "高风险主张仍缺少 ENTAILED 证据，不能直接批准",
                    status_code=409,
                    details={"claim_ids": [item.claim_key for item in unresolved]},
                )
            action = TrustAction.DOWNGRADE if delete_ids or downgrade_ids else TrustAction.RELEASE
        elif payload.decision == "downgrade":
            action = TrustAction.DOWNGRADE
        elif payload.decision == "reject":
            action = TrustAction.BLOCK
        else:
            action = TrustAction.REVIEW

        if action in {TrustAction.RELEASE, TrustAction.DOWNGRADE}:
            for claim in claims:
                claim.released = claim.claim_key not in delete_ids | downgrade_ids and (
                    claim.verification_status == VerificationLabel.ENTAILED
                    or claim.boundary == "pending_confirmation"
                )
        else:
            for claim in claims:
                claim.released = False

        now = datetime.now(UTC)
        review = HumanReviewRecord(
            workspace_id=self.workspace_id,
            solution_run_id=run.id,
            reviewer_id=self.user_id,
            expected_decision_version=payload.expected_decision_version,
            decision=payload.decision,
            edits=payload.edits,
            reason=payload.reason,
            evidence_changes=payload.evidence_changes,
            reviewed_at=now,
        )
        self.session.add(review)
        await self.session.flush()
        decision = TrustDecisionRecord(
            workspace_id=self.workspace_id,
            solution_run_id=run.id,
            version=current.version + 1,
            action=action,
            reason_codes=[f"HUMAN_{payload.decision.upper()}"],
            gate_policy_version=GATE_POLICY_VERSION,
            threshold_version=THRESHOLD_VERSION,
            decided_by="human",
            decision_details={
                "review_id": str(review.id),
                "before": before,
                "edits": payload.edits,
                "evidence_changes": payload.evidence_changes,
            },
        )
        self.session.add(decision)
        await self._propagate_presentation_state(run.id, action)
        if payload.decision == "revise":
            await self._queue_retry(run)
        await self.session.commit()
        return self._decision_data(decision)

    async def retry(self, run_id: uuid.UUID) -> SolutionRun:
        run = await self.get_run(run_id)
        if not run.retryable and run.status != ProcessStatus.COMPLETED:
            raise AppError(ErrorCode.VALIDATION_FAILED, "当前运行不可重试", status_code=409)
        await self._queue_retry(run)
        await self.session.commit()
        return run

    async def _queue_retry(self, run: SolutionRun) -> None:
        task = await self.session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.workspace_id == self.workspace_id,
                WorkflowTask.kind == "solution_run",
                WorkflowTask.target_id == run.id,
                WorkflowTask.is_deleted.is_(False),
            )
        )
        if task is None:
            task = WorkflowTask(
                workspace_id=self.workspace_id,
                kind="solution_run",
                target_id=run.id,
                status=WorkflowTaskStatus.QUEUED,
                stage="queued",
                trace_id=run.trace_id,
                payload={},
                attempt_count=0,
                max_attempts=3,
                available_at=datetime.now(UTC),
            )
            self.session.add(task)
        else:
            task.status = WorkflowTaskStatus.QUEUED
            task.stage = "queued"
            task.available_at = datetime.now(UTC)
            task.attempt_count = 0
            task.error_code = None
            task.error_summary = None
            task.finished_at = None
            task.lease_owner = None
            task.lease_expires_at = None
        run.result_version += 1
        run.status = ProcessStatus.PENDING
        run.stage = "queued"
        run.error_code = None
        run.retryable = False
        run.completed_at = None
        run.deadline_at = datetime.now(UTC) + timedelta(seconds=60)
        task.deadline_at = run.deadline_at

    async def _latest_claims(self, run_id: uuid.UUID) -> list[ClaimRecord]:
        run = await self.get_run(run_id)
        return list(
            (
                await self.session.scalars(
                    select(ClaimRecord)
                    .where(
                        ClaimRecord.solution_run_id == run_id,
                        ClaimRecord.workspace_id == self.workspace_id,
                        ClaimRecord.candidate_version == run.result_version,
                        ClaimRecord.is_deleted.is_(False),
                    )
                    .order_by(ClaimRecord.created_at.asc())
                )
            ).all()
        )

    async def _propagate_presentation_state(self, run_id: uuid.UUID, action: TrustAction) -> None:
        presentations = list(
            (
                await self.session.scalars(
                    select(PresentationRun).where(
                        PresentationRun.solution_run_id == run_id,
                        PresentationRun.workspace_id == self.workspace_id,
                        PresentationRun.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        for item in presentations:
            if action == TrustAction.BLOCK:
                item.status = PresentationStatus.BLOCKED
            elif action not in {TrustAction.RELEASE, TrustAction.DOWNGRADE}:
                item.status = PresentationStatus.STALE

    @staticmethod
    def _decision_data(decision: TrustDecisionRecord) -> dict:
        return {
            "action": decision.action.value,
            "reason_codes": decision.reason_codes,
            "policy_version": decision.gate_policy_version,
            "threshold_version": decision.threshold_version,
            "version": decision.version,
            "decided_by": decision.decided_by,
            "created_at": decision.created_at.isoformat(),
        }


def _evidence_still_visible(
    evidence: EvidenceRecord, current_sources: dict[uuid.UUID, Source]
) -> bool:
    source = current_sources.get(evidence.source_id)
    return bool(
        source
        and evidence.permission_status == "granted"
        and source.status == SourceStatus.COMPLETED
        and source.freshness_status == SourceFreshness.CURRENT
        and source.content_version == evidence.source_version
        and evidence.reviewed_source_version == source.content_version
    )
