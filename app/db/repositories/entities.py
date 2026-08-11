from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.db.models import (
    AIRunRecord,
    Capability,
    ClaimEvidenceLink,
    ClaimRecord,
    CustomerProfile,
    EvidenceRecord,
    Experience,
    ExpertCollaboration,
    ExpertContribution,
    ExpertReply,
    ExportArtifact,
    HtmlArtifact,
    HumanReviewRecord,
    Job,
    Message,
    PresentationInputSnapshot,
    PresentationRenderSnapshot,
    PresentationRun,
    QualityAttemptRecord,
    ReferenceDeck,
    ResearchStep,
    ResearchTask,
    RetrievalSnapshotRecord,
    ReviewRecord,
    Session,
    SolutionRun,
    Source,
    StyleProfile,
    StyleTemplateVersion,
    TrustDecisionRecord,
    User,
    WorkflowTask,
)
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_feishu_user_id(self, feishu_user_id: str) -> User | None:
        statement = select(User).where(
            User.feishu_user_id == feishu_user_id,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def get_by_name(self, name: str) -> User | None:
        statement = select(User).where(User.name == name, *self._active_filters())
        return await self.session.scalar(statement)

    async def list_by_ids(self, user_ids: list) -> list[User]:
        if not user_ids:
            return []
        statement = select(User).where(User.id.in_(user_ids), *self._active_filters())
        return list((await self.session.scalars(statement)).all())


class SourceRepository(BaseRepository[Source]):
    model = Source

    async def get_by_source_url(self, source_url: str) -> Source | None:
        statement = select(Source).where(
            Source.source_url == source_url,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str | None = None,
        status: str | None = None,
        purpose: str | None = None,
    ) -> list[Source]:
        filters = self._source_filters(keyword=keyword, status=status, purpose=purpose)
        statement = (
            select(Source)
            .where(*filters)
            .order_by(Source.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        purpose: str | None = None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Source)
            .where(*self._source_filters(keyword=keyword, status=status, purpose=purpose))
        )
        return int(await self.session.scalar(statement) or 0)

    def _source_filters(
        self, *, keyword: str | None, status: str | None, purpose: str | None
    ) -> tuple:
        filters = list(self._active_filters())
        if keyword:
            pattern = f"%{keyword}%"
            filters.append(or_(Source.title.ilike(pattern), Source.content.ilike(pattern)))
        if status:
            filters.append(Source.status == status)
        if purpose:
            filters.append(Source.purpose == purpose)
        return tuple(filters)


class CustomerProfileRepository(BaseRepository[CustomerProfile]):
    model = CustomerProfile

    async def get(self, entity_id):
        statement = (
            select(CustomerProfile)
            .options(selectinload(CustomerProfile.sources))
            .where(CustomerProfile.id == entity_id, *self._active_filters())
        )
        return await self.session.scalar(statement)

    async def get_by_customer_name(self, customer_name: str) -> CustomerProfile | None:
        statement = (
            select(CustomerProfile)
            .options(selectinload(CustomerProfile.sources))
            .where(CustomerProfile.customer_name == customer_name, *self._active_filters())
        )
        return await self.session.scalar(statement)

    async def list_filtered(
        self, *, offset: int, limit: int, keyword: str | None = None
    ) -> list[CustomerProfile]:
        filters = list(self._active_filters())
        if keyword:
            filters.append(CustomerProfile.customer_name.ilike(f"%{keyword}%"))
        statement = (
            select(CustomerProfile)
            .options(selectinload(CustomerProfile.sources))
            .where(*filters)
            .order_by(CustomerProfile.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(self, *, keyword: str | None = None) -> int:
        filters = list(self._active_filters())
        if keyword:
            filters.append(CustomerProfile.customer_name.ilike(f"%{keyword}%"))
        statement = select(func.count()).select_from(CustomerProfile).where(*filters)
        return int(await self.session.scalar(statement) or 0)


class ExperienceRepository(BaseRepository[Experience]):
    model = Experience

    async def get_by_source(self, source_id) -> Experience | None:
        statement = select(Experience).where(
            Experience.source_id == source_id, *self._active_filters()
        )
        return await self.session.scalar(statement)

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str | None = None,
        review_status: str | None = None,
    ) -> list[Experience]:
        statement = (
            select(Experience)
            .where(*self._asset_filters(keyword, review_status))
            .order_by(Experience.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(
        self, *, keyword: str | None = None, review_status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Experience)
            .where(*self._asset_filters(keyword, review_status))
        )
        return int(await self.session.scalar(statement) or 0)

    def _asset_filters(self, keyword: str | None, review_status: str | None) -> tuple:
        filters = list(self._active_filters())
        if keyword:
            filters.append(cast(Experience.data, String).ilike(f"%{keyword}%"))
        if review_status:
            filters.append(Experience.review_status == review_status)
        return tuple(filters)


class CapabilityRepository(BaseRepository[Capability]):
    model = Capability

    async def list_by_source(self, source_id) -> list[Capability]:
        statement = select(Capability).where(
            Capability.source_id == source_id, *self._active_filters()
        )
        return list((await self.session.scalars(statement)).all())

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str | None = None,
        review_status: str | None = None,
    ) -> list[Capability]:
        statement = (
            select(Capability)
            .where(*self._asset_filters(keyword, review_status))
            .order_by(Capability.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(
        self, *, keyword: str | None = None, review_status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Capability)
            .where(*self._asset_filters(keyword, review_status))
        )
        return int(await self.session.scalar(statement) or 0)

    def _asset_filters(self, keyword: str | None, review_status: str | None) -> tuple:
        filters = list(self._active_filters())
        if keyword:
            filters.append(cast(Capability.data, String).ilike(f"%{keyword}%"))
        if review_status:
            filters.append(Capability.review_status == review_status)
        return tuple(filters)


class SessionRepository(BaseRepository[Session]):
    model = Session

    async def list_filtered(self, *, offset: int, limit: int, profile_id=None) -> list[Session]:
        filters = list(self._active_filters())
        if profile_id is not None:
            filters.append(Session.customer_profile_id == profile_id)
        statement = (
            select(Session)
            .where(*filters)
            .order_by(Session.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(self, *, profile_id=None) -> int:
        filters = list(self._active_filters())
        if profile_id is not None:
            filters.append(Session.customer_profile_id == profile_id)
        statement = select(func.count()).select_from(Session).where(*filters)
        return int(await self.session.scalar(statement) or 0)


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def list_for_session(self, session_id) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.session_id == session_id, *self._active_filters())
            .order_by(Message.sequence.asc())
        )
        return list((await self.session.scalars(statement)).all())

    async def next_sequence(self, session_id) -> int:
        statement = select(func.max(Message.sequence)).where(
            Message.session_id == session_id, *self._active_filters()
        )
        return int(await self.session.scalar(statement) or 0) + 1

    async def get_for_solution_run(self, run_id) -> Message | None:
        statement = select(Message).where(
            Message.solution_run_id == run_id, *self._active_filters()
        )
        return await self.session.scalar(statement)


class SolutionRunRepository(BaseRepository[SolutionRun]):
    model = SolutionRun


class RetrievalSnapshotRecordRepository(BaseRepository[RetrievalSnapshotRecord]):
    model = RetrievalSnapshotRecord


class ClaimRecordRepository(BaseRepository[ClaimRecord]):
    model = ClaimRecord

    async def list_for_run(self, run_id) -> list[ClaimRecord]:
        statement = (
            select(ClaimRecord)
            .where(ClaimRecord.solution_run_id == run_id, *self._active_filters())
            .order_by(ClaimRecord.created_at.asc())
        )
        return list((await self.session.scalars(statement)).all())


class EvidenceRecordRepository(BaseRepository[EvidenceRecord]):
    model = EvidenceRecord

    async def list_for_run(self, run_id) -> list[EvidenceRecord]:
        statement = (
            select(EvidenceRecord)
            .where(EvidenceRecord.solution_run_id == run_id, *self._active_filters())
            .order_by(EvidenceRecord.created_at.asc())
        )
        return list((await self.session.scalars(statement)).all())


class ClaimEvidenceLinkRepository(BaseRepository[ClaimEvidenceLink]):
    model = ClaimEvidenceLink

    async def list_for_run(self, run_id) -> list[ClaimEvidenceLink]:
        statement = select(ClaimEvidenceLink).where(
            ClaimEvidenceLink.solution_run_id == run_id, *self._active_filters()
        )
        return list((await self.session.scalars(statement)).all())


class QualityAttemptRecordRepository(BaseRepository[QualityAttemptRecord]):
    model = QualityAttemptRecord

    async def list_for_run(self, run_id) -> list[QualityAttemptRecord]:
        statement = (
            select(QualityAttemptRecord)
            .where(QualityAttemptRecord.solution_run_id == run_id, *self._active_filters())
            .order_by(QualityAttemptRecord.attempt.asc())
        )
        return list((await self.session.scalars(statement)).all())


class TrustDecisionRecordRepository(BaseRepository[TrustDecisionRecord]):
    model = TrustDecisionRecord

    async def latest_for_run(self, run_id) -> TrustDecisionRecord | None:
        statement = (
            select(TrustDecisionRecord)
            .where(TrustDecisionRecord.solution_run_id == run_id, *self._active_filters())
            .order_by(TrustDecisionRecord.version.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)


class HumanReviewRecordRepository(BaseRepository[HumanReviewRecord]):
    model = HumanReviewRecord

    async def list_for_run(self, run_id) -> list[HumanReviewRecord]:
        statement = (
            select(HumanReviewRecord)
            .where(HumanReviewRecord.solution_run_id == run_id, *self._active_filters())
            .order_by(HumanReviewRecord.reviewed_at.asc())
        )
        return list((await self.session.scalars(statement)).all())


class WorkflowTaskRepository(BaseRepository[WorkflowTask]):
    model = WorkflowTask

    async def get_for_target(self, kind: str, target_id) -> WorkflowTask | None:
        statement = select(WorkflowTask).where(
            WorkflowTask.kind == kind,
            WorkflowTask.target_id == target_id,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)


class ReferenceDeckRepository(BaseRepository[ReferenceDeck]):
    model = ReferenceDeck


class StyleProfileRepository(BaseRepository[StyleProfile]):
    model = StyleProfile


class StyleTemplateVersionRepository(BaseRepository[StyleTemplateVersion]):
    model = StyleTemplateVersion


class PresentationRunRepository(BaseRepository[PresentationRun]):
    model = PresentationRun

    async def list_for_solution(self, run_id) -> list[PresentationRun]:
        statement = select(PresentationRun).where(
            PresentationRun.solution_run_id == run_id, *self._active_filters()
        )
        return list((await self.session.scalars(statement)).all())


class PresentationInputSnapshotRepository(BaseRepository[PresentationInputSnapshot]):
    model = PresentationInputSnapshot


class PresentationRenderSnapshotRepository(BaseRepository[PresentationRenderSnapshot]):
    model = PresentationRenderSnapshot

    async def get_for_version(
        self, presentation_id, version: int
    ) -> PresentationRenderSnapshot | None:
        statement = select(PresentationRenderSnapshot).where(
            PresentationRenderSnapshot.presentation_id == presentation_id,
            PresentationRenderSnapshot.version == version,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def create_or_verify(self, **values) -> PresentationRenderSnapshot:
        existing = await self.get_for_version(values["presentation_id"], values["version"])
        if existing is None:
            return await self.create(**values)
        immutable_fields = (
            "schema_version",
            "fact_ledger_hash",
            "positioned_spec_hash",
            "compiled_style_hash",
            "render_ir_hash",
            "fact_ledger_json",
            "render_ir_json",
            "renderer_versions",
            "diagnostics",
        )
        if any(getattr(existing, field) != values[field] for field in immutable_fields):
            raise ValueError("render snapshot version already exists with different content")
        return existing

    async def update(self, entity, **values):
        raise TypeError("presentation render snapshots are immutable")

    async def soft_delete(self, entity) -> None:
        raise TypeError("presentation render snapshots cannot be soft deleted")


class HtmlArtifactRepository(BaseRepository[HtmlArtifact]):
    model = HtmlArtifact

    async def latest_for_presentation(self, presentation_id) -> HtmlArtifact | None:
        statement = (
            select(HtmlArtifact)
            .where(HtmlArtifact.presentation_id == presentation_id, *self._active_filters())
            .order_by(HtmlArtifact.version.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)


class ExportArtifactRepository(BaseRepository[ExportArtifact]):
    model = ExportArtifact


class JobRepository(BaseRepository[Job]):
    model = Job


class ReviewRecordRepository(BaseRepository[ReviewRecord]):
    model = ReviewRecord


class AIRunRecordRepository(BaseRepository[AIRunRecord]):
    model = AIRunRecord


class ResearchTaskRepository(BaseRepository[ResearchTask]):
    model = ResearchTask

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        profile_id=None,
    ) -> list[ResearchTask]:
        filters = list(self._active_filters())
        if status:
            filters.append(ResearchTask.status == status)
        if profile_id is not None:
            filters.append(ResearchTask.customer_profile_id == profile_id)
        statement = (
            select(ResearchTask)
            .where(*filters)
            .order_by(ResearchTask.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(self, *, status: str | None = None, profile_id=None) -> int:
        filters = list(self._active_filters())
        if status:
            filters.append(ResearchTask.status == status)
        if profile_id is not None:
            filters.append(ResearchTask.customer_profile_id == profile_id)
        statement = select(func.count()).select_from(ResearchTask).where(*filters)
        return int(await self.session.scalar(statement) or 0)


class ResearchStepRepository(BaseRepository[ResearchStep]):
    model = ResearchStep

    async def list_for_task(self, task_id) -> list[ResearchStep]:
        statement = (
            select(ResearchStep)
            .where(ResearchStep.research_task_id == task_id, *self._active_filters())
            .order_by(ResearchStep.sequence.asc())
        )
        return list((await self.session.scalars(statement)).all())


class ExpertContributionRepository(BaseRepository[ExpertContribution]):
    model = ExpertContribution

    async def get_for_user_source_role(self, user_id, source_id, role) -> ExpertContribution | None:
        statement = select(ExpertContribution).where(
            ExpertContribution.user_id == user_id,
            ExpertContribution.source_id == source_id,
            ExpertContribution.role == role,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def list_for_sources(self, source_ids: list) -> list[ExpertContribution]:
        if not source_ids:
            return []
        statement = (
            select(ExpertContribution)
            .where(ExpertContribution.source_id.in_(source_ids), *self._active_filters())
            .order_by(ExpertContribution.updated_at.desc())
        )
        return list((await self.session.scalars(statement)).all())


class ExpertCollaborationRepository(BaseRepository[ExpertCollaboration]):
    model = ExpertCollaboration

    async def get_active_for_task(self, task_id) -> ExpertCollaboration | None:
        statement = (
            select(ExpertCollaboration)
            .where(
                ExpertCollaboration.research_task_id == task_id,
                ExpertCollaboration.status.in_(
                    ["draft", "awaiting_confirmation", "group_created", "sent"]
                ),
                *self._active_filters(),
            )
            .order_by(ExpertCollaboration.created_at.desc())
        )
        return await self.session.scalar(statement)

    async def get_by_feishu_group_id(self, group_id: str) -> ExpertCollaboration | None:
        statement = select(ExpertCollaboration).where(
            ExpertCollaboration.feishu_group_id == group_id,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def list_filtered(self, *, offset: int, limit: int) -> list[ExpertCollaboration]:
        statement = (
            select(ExpertCollaboration)
            .where(*self._active_filters())
            .order_by(ExpertCollaboration.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(self) -> int:
        statement = (
            select(func.count()).select_from(ExpertCollaboration).where(*self._active_filters())
        )
        return int(await self.session.scalar(statement) or 0)


class ExpertReplyRepository(BaseRepository[ExpertReply]):
    model = ExpertReply

    async def get_by_message_id(self, message_id: str) -> ExpertReply | None:
        statement = select(ExpertReply).where(
            ExpertReply.feishu_message_id == message_id, *self._active_filters()
        )
        return await self.session.scalar(statement)

    async def list_for_task(self, task_id) -> list[ExpertReply]:
        statement = (
            select(ExpertReply)
            .where(ExpertReply.research_task_id == task_id, *self._active_filters())
            .order_by(ExpertReply.created_at.asc())
        )
        return list((await self.session.scalars(statement)).all())
