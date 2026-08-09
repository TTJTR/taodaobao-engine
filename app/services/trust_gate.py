import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ClaimEvidenceLink,
    ClaimRecord,
    EvidenceRecord,
    QualityAttemptRecord,
    Source,
    SourceFreshness,
    SourceStatus,
    TrustAction,
    TrustDecisionRecord,
    VerificationLabel,
)
from app.schemas.trust import SolutionV2Payload

GATE_POLICY_VERSION = "trust-gate-v1"
THRESHOLD_VERSION = "trust-threshold-v1"


@dataclass(frozen=True)
class EvidenceState:
    evidence_key: str
    in_snapshot: bool
    location_reproducible: bool
    reviewed_version_current: bool
    permission_valid: bool


@dataclass(frozen=True)
class GateResult:
    action: TrustAction
    reason_codes: tuple[str, ...]
    released_claim_ids: frozenset[str]


def evaluate_trust_gate(
    payload: SolutionV2Payload,
    evidence_states: dict[str, EvidenceState],
    *,
    deadline_exceeded: bool = False,
    unresolved_conflicts: bool = False,
    human_override_requested: bool = False,
    has_complete_human_audit: bool = False,
) -> GateResult:
    """Apply deterministic backend policy. AI recommended_action is intentionally ignored."""
    hard_block: list[str] = []
    review: list[str] = []
    downgrade: list[str] = []
    released: set[str] = set()

    for evidence in payload.evidence:
        state = evidence_states.get(evidence.evidence_key)
        if state is None or not state.in_snapshot:
            hard_block.append("TG02_EVIDENCE_OUTSIDE_SNAPSHOT")
            continue
        if not state.location_reproducible:
            review.append("TG03_EVIDENCE_LOCATION_INVALID")
        if not state.reviewed_version_current:
            review.append("TG04_EVIDENCE_VERSION_STALE")
        if not state.permission_valid:
            hard_block.append("TG05_EVIDENCE_PERMISSION_REVOKED")

    for claim in payload.claims:
        label = claim.verification_status
        if label == "entailed":
            released.add(claim.claim_id)
            continue
        if claim.boundary == "pending_confirmation" and label == "insufficient":
            released.add(claim.claim_id)
            continue
        if label == "contradicted":
            code = "TG07_UNRESOLVED_CONTRADICTION"
            (hard_block if claim.risk_level == "high" else review).append(code)
        elif claim.risk_level == "high":
            downgrade.append("TG06_HIGH_RISK_NOT_ENTAILED")
        else:
            review.append("TG06_CLAIM_NOT_ENTAILED")

    if unresolved_conflicts:
        review.append("TG07_SNAPSHOT_CONFLICT")
    if deadline_exceeded:
        review.append("TG08_DEADLINE_EXCEEDED")
    if human_override_requested and not has_complete_human_audit:
        hard_block.append("TG08_HUMAN_OVERRIDE_AUDIT_MISSING")

    if hard_block:
        action = TrustAction.BLOCK
        released.clear()
    elif review:
        action = TrustAction.REVIEW
        released.clear()
    elif downgrade:
        action = TrustAction.DOWNGRADE
    else:
        action = TrustAction.RELEASE
    reasons = tuple(dict.fromkeys([*hard_block, *review, *downgrade]))
    return GateResult(action=action, reason_codes=reasons, released_claim_ids=frozenset(released))


class TrustPersistenceService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def persist_and_gate(
        self,
        run,
        payload: SolutionV2Payload,
        raw_snapshot: dict[str, Any],
    ) -> TrustDecisionRecord:
        snapshot_items = {
            f"{item['id']}/{item['source_id']}": item
            for item in [
                *raw_snapshot.get("experiences", []),
                *raw_snapshot.get("capabilities", []),
            ]
        }
        source_ids = {uuid.UUID(item.source_id) for item in payload.evidence}
        sources = {
            str(item.id): item
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
        evidence_rows: dict[str, EvidenceRecord] = {}
        evidence_states: dict[str, EvidenceState] = {}
        for evidence in payload.evidence:
            item = snapshot_items.get(evidence.evidence_key)
            source = sources.get(evidence.source_id)
            location = (item or {}).get("evidence_location") or {}
            source_version = int((item or {}).get("source_version") or 0)
            reviewed_version = (item or {}).get("reviewed_source_version")
            permission_valid = bool(
                source
                and source.status == SourceStatus.COMPLETED
                and source.freshness_status == SourceFreshness.CURRENT
                and not source.is_deleted
            )
            state = EvidenceState(
                evidence_key=evidence.evidence_key,
                in_snapshot=item is not None,
                location_reproducible=bool(
                    (item or {}).get("evidence_quote")
                    and location.get("kind")
                    and location.get("path")
                ),
                reviewed_version_current=bool(
                    source
                    and reviewed_version is not None
                    and int(reviewed_version) == source.content_version == source_version
                ),
                permission_valid=permission_valid,
            )
            evidence_states[evidence.evidence_key] = state
            if item is None:
                continue
            row = EvidenceRecord(
                workspace_id=self.workspace_id,
                solution_run_id=run.id,
                evidence_key=evidence.evidence_key,
                candidate_version=run.result_version,
                asset_id=uuid.UUID(evidence.asset_id),
                source_id=uuid.UUID(evidence.source_id),
                source_version=source_version,
                reviewed_source_version=(int(reviewed_version) if reviewed_version else None),
                quote=str(item.get("evidence_quote") or ""),
                location=location,
                permission_status="granted" if permission_valid else "revoked",
                permission_checked_at=(source.permission_checked_at if source else None),
            )
            self.session.add(row)
            evidence_rows[evidence.evidence_key] = row
        await self.session.flush()

        claim_rows: dict[str, ClaimRecord] = {}
        for claim in payload.claims:
            row = ClaimRecord(
                workspace_id=self.workspace_id,
                solution_run_id=run.id,
                claim_key=claim.claim_id,
                section=claim.section,
                claim_text=claim.text,
                claim_type=claim.claim_type,
                boundary=claim.boundary,
                risk_level=claim.risk_level,
                verification_status=VerificationLabel(claim.verification_status),
                released=False,
                candidate_version=run.result_version,
            )
            self.session.add(row)
            claim_rows[claim.claim_id] = row
        await self.session.flush()

        for link in payload.claim_evidence_links:
            claim = claim_rows.get(link.claim_id)
            evidence = evidence_rows.get(link.evidence_key)
            if claim is None or evidence is None:
                continue
            self.session.add(
                ClaimEvidenceLink(
                    workspace_id=self.workspace_id,
                    solution_run_id=run.id,
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    label=VerificationLabel(link.label),
                    score=link.score,
                    verifier_version=link.verifier_version,
                )
            )
        for attempt in payload.quality_attempts:
            self.session.add(
                QualityAttemptRecord(
                    workspace_id=self.workspace_id,
                    solution_run_id=run.id,
                    attempt=attempt.attempt,
                    candidate_version=attempt.candidate_version,
                    failed_claim_ids=attempt.failed_claim_ids,
                    report=attempt.quality_report,
                    duration_ms=attempt.duration_ms,
                    verifier_version=payload.verifier_version,
                )
            )

        gate = evaluate_trust_gate(
            payload,
            evidence_states,
            deadline_exceeded=bool(run.deadline_at and datetime.now(UTC) > run.deadline_at),
            unresolved_conflicts=bool(raw_snapshot.get("conflicts")),
        )
        for claim_id, row in claim_rows.items():
            row.released = claim_id in gate.released_claim_ids
        latest_version = await self.session.scalar(
            select(TrustDecisionRecord.version)
            .where(
                TrustDecisionRecord.workspace_id == self.workspace_id,
                TrustDecisionRecord.solution_run_id == run.id,
                TrustDecisionRecord.is_deleted.is_(False),
            )
            .order_by(TrustDecisionRecord.version.desc())
            .limit(1)
        )
        decision = TrustDecisionRecord(
            workspace_id=self.workspace_id,
            solution_run_id=run.id,
            version=int(latest_version or 0) + 1,
            action=gate.action,
            reason_codes=list(gate.reason_codes),
            gate_policy_version=GATE_POLICY_VERSION,
            threshold_version=THRESHOLD_VERSION,
            decided_by="system",
            decision_details={
                "candidate_solution": payload.solution.model_dump(mode="json"),
                "ai_recommended_action": payload.recommended_action,
                "released_claim_ids": sorted(gate.released_claim_ids),
                "verifier_version": payload.verifier_version,
            },
        )
        self.session.add(decision)
        await self.session.flush()
        return decision


def build_safe_solution(
    payload: SolutionV2Payload,
    gate: TrustDecisionRecord,
    claims: list[ClaimRecord],
) -> dict[str, Any]:
    solution = payload.solution.model_dump(mode="json")
    if gate.action in {TrustAction.RELEASE, TrustAction.DOWNGRADE}:
        allowed = {claim.claim_key for claim in claims if claim.released}
        by_section: dict[str, list[ClaimRecord]] = {}
        for claim in claims:
            by_section.setdefault(claim.section, []).append(claim)
        used_pairs: set[tuple[str, str]] = set()
        for section in by_section:
            kept = []
            for index, item in enumerate(solution.get(section, []), start=1):
                claim_key = f"{section}:{index}"
                if claim_key in allowed:
                    kept.append(item)
                    if item.get("asset_id") and item.get("source_id"):
                        used_pairs.add((item["asset_id"], item["source_id"]))
            solution[section] = kept
        solution["sources"] = [
            item
            for item in solution.get("sources", [])
            if (item.get("asset_id"), item.get("source_id")) in used_pairs
        ]
        return solution
    return {
        "requirement_understanding": [
            {
                "text": "本次方案尚未通过可信发布门禁，不能作为正式企业结论使用。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [],
        "historical_evidence": [],
        "capability_composition": [],
        "prerequisites_and_risks": [],
        "pending_confirmations": [
            {
                "text": "请根据可信报告完成证据补充或人工审核。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "sources": [],
        "suggested_questions": ["需要补充哪些证据才能完成发布审核？"],
    }
