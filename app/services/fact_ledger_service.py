import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import ClaimEvidenceLink, ClaimRecord, EvidenceRecord, SolutionRun
from app.schemas.presentation import FactAtom, FactLedger, LedgerEvidence


class FactLedgerService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def build_ledger(self, run_id: uuid.UUID) -> FactLedger:
        run = await self.session.scalar(
            select(SolutionRun).where(
                SolutionRun.id == run_id,
                SolutionRun.workspace_id == self.workspace_id,
                SolutionRun.is_deleted.is_(False),
            )
        )
        if run is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "方案运行不存在", status_code=404)

        statement = (
            select(ClaimRecord, EvidenceRecord)
            .join(
                ClaimEvidenceLink,
                ClaimEvidenceLink.claim_id == ClaimRecord.id,
            )
            .join(
                EvidenceRecord,
                EvidenceRecord.id == ClaimEvidenceLink.evidence_id,
            )
            .where(
                ClaimRecord.solution_run_id == run_id,
                ClaimRecord.workspace_id == self.workspace_id,
                ClaimRecord.candidate_version == run.result_version,
                ClaimRecord.released.is_(True),
                ClaimRecord.is_deleted.is_(False),
                ClaimEvidenceLink.solution_run_id == run_id,
                ClaimEvidenceLink.workspace_id == self.workspace_id,
                ClaimEvidenceLink.is_deleted.is_(False),
                EvidenceRecord.solution_run_id == run_id,
                EvidenceRecord.workspace_id == self.workspace_id,
                EvidenceRecord.candidate_version == run.result_version,
                EvidenceRecord.permission_status == "granted",
                EvidenceRecord.is_deleted.is_(False),
            )
            .order_by(ClaimRecord.created_at.asc(), EvidenceRecord.created_at.asc())
        )
        rows = (await self.session.execute(statement)).all()

        evidence_by_claim: dict[uuid.UUID, list[LedgerEvidence]] = defaultdict(list)
        claims: dict[uuid.UUID, ClaimRecord] = {}
        for claim, evidence in rows:
            claims[claim.id] = claim
            evidence_by_claim[claim.id].append(
                LedgerEvidence(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    source_version=evidence.source_version,
                    quote=evidence.quote,
                )
            )

        return FactLedger(
            run_id=run_id,
            facts=tuple(
                FactAtom(
                    claim_id=claim.id,
                    claim_key=claim.claim_key,
                    verbatim_text=claim.claim_text,
                    boundary=claim.boundary,
                    evidence=tuple(evidence_by_claim[claim.id]),
                    allowed_labels=(claim.claim_key,),
                )
                for claim in claims.values()
            ),
        )
