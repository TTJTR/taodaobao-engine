import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.token_crypto import EncryptedTokenText


class SourceType(StrEnum):
    FEISHU_DOC = "feishu_doc"
    PASTED_TEXT = "pasted_text"


class SourcePurpose(StrEnum):
    CUSTOMER_PROFILE = "customer_profile"
    EXPERIENCE = "experience"
    CAPABILITY = "capability"


class SourceStatus(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SOURCE_UPDATED = "source_updated"


class ProfileStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"


class ProcessStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(StrEnum):
    SOURCE_PROCESSING = "source_processing"
    PROFILE_GENERATION = "profile_generation"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class SourceFreshness(StrEnum):
    CURRENT = "current"
    UPDATED = "updated"
    PERMISSION_DENIED = "permission_denied"
    DELETED = "deleted"


class ResearchTaskStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RESEARCHING = "researching"
    WAITING_EXPERT = "waiting_expert"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollaborationStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    GROUP_CREATED = "group_created"
    SENT = "sent"
    CLOSED = "closed"
    FAILED = "failed"


class ExpertQuestionStatus(StrEnum):
    OPEN = "open"
    INVITED = "invited"
    ANSWERED = "answered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CLOSED = "closed"


class ContributionRole(StrEnum):
    SOURCE_AUTHOR = "source_author"
    ASSET_CONTRIBUTOR = "asset_contributor"
    REVIEWER = "reviewer"


class TrustAction(StrEnum):
    PENDING = "pending"
    RELEASE = "release"
    DOWNGRADE = "downgrade"
    REVIEW = "review"
    BLOCK = "block"


class VerificationLabel(StrEnum):
    ENTAILED = "entailed"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    INVALID = "invalid"


class WorkflowTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReferenceDeckStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    PROFILE_DRAFT = "profile_draft"
    PROFILE_CONFIRMED = "profile_confirmed"
    FAILED = "failed"


class StyleProfileStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class PresentationStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PLANNING = "planning"
    RENDERING = "rendering"
    VALIDATING = "validating"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    STALE = "stale"
    BLOCKED = "blocked"


class ExportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class SearchRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RawArtifactKind(StrEnum):
    WEB_PAGE = "web_page"
    TENDER_FILE = "tender_file"
    PASTED_TEXT = "pasted_text"


class RawArtifactStatus(StrEnum):
    CAPTURED = "captured"
    VALIDATED = "validated"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class ArtifactRelationType(StrEnum):
    PRIMARY = "primary"
    DUPLICATE = "duplicate"
    CORROBORATING = "corroborating"
    CONFLICTING = "conflicting"


class TenderParseStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"
    REJECTED = "rejected"


class TenderRequirementStatus(StrEnum):
    AI_DRAFT = "ai_draft"
    EDITED = "edited"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class IntelligenceFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    DELETED = "deleted"


class IntelligenceReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ProposalStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ResponseEvidenceStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTED = "conflicted"
    STALE = "stale"
    PENDING_CONFIRMATION = "pending_confirmation"


class RehearsalStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


def enum_column(enum_type: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: JSONB}


class EntityMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True
    )


class WorkspaceMixin:
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)


class User(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("workspace_id", "feishu_user_id", name="uq_users_workspace_feishu"),
    )

    feishu_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(1000))
    feishu_tenant_key: Mapped[str | None] = mapped_column(String(128))
    feishu_access_token: Mapped[str | None] = mapped_column(EncryptedTokenText())
    feishu_refresh_token: Mapped[str | None] = mapped_column(EncryptedTokenText())
    feishu_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    imported_sources: Mapped[list["Source"]] = relationship(
        back_populates="imported_by", foreign_keys="Source.imported_by_id"
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="created_by", foreign_keys="Session.created_by_id"
    )


class CustomerProfile(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "customer_profiles"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "customer_name", name="uq_customer_profiles_workspace_name"
        ),
    )

    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ProfileStatus] = mapped_column(
        enum_column(ProfileStatus, "profile_status"),
        nullable=False,
        default=ProfileStatus.PENDING_CONFIRMATION,
    )
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sources: Mapped[list["Source"]] = relationship(back_populates="customer_profile")
    sessions: Mapped[list["Session"]] = relationship(back_populates="customer_profile")

    @property
    def source_ids(self) -> list[uuid.UUID]:
        return [source.id for source in self.sources if not source.is_deleted]


class Source(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_url", name="uq_sources_workspace_url"),
        Index("ix_sources_workspace_status", "workspace_id", "status"),
    )

    customer_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_profiles.id", ondelete="SET NULL"), index=True
    )
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    imported_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    type: Mapped[SourceType] = mapped_column(enum_column(SourceType, "source_type"), nullable=False)
    purpose: Mapped[SourcePurpose] = mapped_column(
        enum_column(SourcePurpose, "source_purpose"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    author: Mapped[str | None] = mapped_column(String(200))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[SourceStatus] = mapped_column(
        enum_column(SourceStatus, "source_status"), nullable=False, default=SourceStatus.PENDING
    )
    is_demo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    freshness_status: Mapped[SourceFreshness] = mapped_column(
        enum_column(SourceFreshness, "source_freshness_status"),
        nullable=False,
        default=SourceFreshness.CURRENT,
        server_default=SourceFreshness.CURRENT.value,
    )
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    content_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    permission_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer_profile: Mapped[CustomerProfile | None] = relationship(back_populates="sources")
    imported_by: Mapped[User] = relationship(
        back_populates="imported_sources", foreign_keys=[imported_by_id]
    )
    experience: Mapped["Experience | None"] = relationship(back_populates="source", uselist=False)
    capabilities: Mapped[list["Capability"]] = relationship(back_populates="source")


class Experience(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "experiences"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_experiences_source"),
        Index("ix_experiences_workspace_review", "workspace_id", "review_status"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, "experience_review_status"),
        nullable=False,
        default=ReviewStatus.PENDING_REVIEW,
    )
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version_at_review: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embedding_text: Mapped[str | None] = mapped_column(Text)
    embedding_version: Mapped[str | None] = mapped_column(String(128))
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    embedding_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    source: Mapped[Source] = relationship(back_populates="experience")


class Capability(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "capabilities"
    __table_args__ = (Index("ix_capabilities_workspace_review", "workspace_id", "review_status"),)

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, "capability_review_status"),
        nullable=False,
        default=ReviewStatus.PENDING_REVIEW,
    )
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version_at_review: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embedding_text: Mapped[str | None] = mapped_column(Text)
    embedding_version: Mapped[str | None] = mapped_column(String(128))
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    embedding_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    source: Mapped[Source] = relationship(back_populates="capabilities")


class Session(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_profile_updated", "customer_profile_id", "updated_at"),)

    customer_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新方案会话")

    customer_profile: Mapped[CustomerProfile] = relationship(back_populates="sessions")
    created_by: Mapped[User] = relationship(back_populates="sessions", foreign_keys=[created_by_id])
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", foreign_keys="Message.session_id", order_by="Message.sequence"
    )
    solution_runs: Mapped[list["SolutionRun"]] = relationship(
        back_populates="session", foreign_keys="SolutionRun.session_id"
    )


class Message(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_messages_session_sequence"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False
    )
    solution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "solution_runs.id",
            name="fk_messages_solution_run",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        enum_column(MessageRole, "message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped[Session] = relationship(back_populates="messages", foreign_keys=[session_id])
    solution_run: Mapped["SolutionRun | None"] = relationship(
        back_populates="response_messages", foreign_keys=[solution_run_id]
    )


class SolutionRun(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "solution_runs"
    __table_args__ = (Index("ix_solution_runs_session_created", "session_id", "created_at"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False
    )
    request_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )
    profile_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retrieval_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    display_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProcessStatus] = mapped_column(
        enum_column(ProcessStatus, "solution_run_status"),
        nullable=False,
        default=ProcessStatus.PENDING,
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(
        String(64), nullable=False, default="queued", server_default="queued"
    )
    trace_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default=lambda: str(uuid.uuid4())
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    result_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    session: Mapped[Session] = relationship(
        back_populates="solution_runs", foreign_keys=[session_id]
    )
    request_message: Mapped[Message] = relationship(foreign_keys=[request_message_id])
    response_messages: Mapped[list[Message]] = relationship(
        back_populates="solution_run", foreign_keys="Message.solution_run_id"
    )


class RetrievalSnapshotRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "retrieval_snapshot_records"
    __table_args__ = (
        UniqueConstraint("solution_run_id", "version", name="uq_retrieval_snapshots_run_version"),
        Index("ix_retrieval_snapshots_run", "solution_run_id", "created_at"),
    )

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False, default="retrieval-v2")
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    permission_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding_version: Mapped[str | None] = mapped_column(String(128))


class ClaimRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "claim_records"
    __table_args__ = (
        UniqueConstraint(
            "solution_run_id", "candidate_version", "claim_key", name="uq_claim_records_run_key"
        ),
        Index("ix_claim_records_run_status", "solution_run_id", "verification_status"),
    )

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    claim_key: Mapped[str] = mapped_column(String(160), nullable=False)
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_text: Mapped[str] = mapped_column("text", Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    boundary: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_status: Mapped[VerificationLabel] = mapped_column(
        enum_column(VerificationLabel, "claim_verification_status"), nullable=False
    )
    released: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EvidenceRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint(
            "solution_run_id",
            "candidate_version",
            "evidence_key",
            name="uq_evidence_records_run_key",
        ),
        Index("ix_evidence_records_source_version", "source_id", "source_version"),
    )

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    evidence_key: Mapped[str] = mapped_column(String(256), nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_source_version: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    permission_status: Mapped[str] = mapped_column(String(32), nullable=False)
    permission_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClaimEvidenceLink(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "claim_evidence_links"
    __table_args__ = (UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence_link"),)

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_records.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_records.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[VerificationLabel] = mapped_column(
        enum_column(VerificationLabel, "claim_evidence_label"), nullable=False
    )
    score: Mapped[float | None]
    verifier_version: Mapped[str] = mapped_column(String(128), nullable=False)


class QualityAttemptRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "quality_attempt_records"
    __table_args__ = (
        UniqueConstraint(
            "solution_run_id", "candidate_version", "attempt", name="uq_quality_attempt_run_attempt"
        ),
    )

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_claim_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    verifier_version: Mapped[str] = mapped_column(String(128), nullable=False)


class TrustDecisionRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "trust_decision_records"
    __table_args__ = (
        UniqueConstraint("solution_run_id", "version", name="uq_trust_decision_run_version"),
        Index("ix_trust_decision_run_created", "solution_run_id", "created_at"),
    )

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[TrustAction] = mapped_column(
        enum_column(TrustAction, "trust_decision_action"), nullable=False
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    gate_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    threshold_version: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    decision_details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class HumanReviewRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "human_review_records"
    __table_args__ = (
        UniqueConstraint(
            "solution_run_id", "expected_decision_version", name="uq_human_review_run_version"
        ),
    )

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expected_decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    edits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_changes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowTask(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (
        UniqueConstraint("kind", "target_id", name="uq_workflow_task_kind_target"),
        Index("ix_workflow_tasks_claim", "status", "available_at", "lease_expires_at"),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[WorkflowTaskStatus] = mapped_column(
        enum_column(WorkflowTaskStatus, "workflow_task_status"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferenceDeck(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "reference_decks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "file_hash", "version", name="uq_reference_deck_hash_version"
        ),
    )

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ReferenceDeckStatus] = mapped_column(
        enum_column(ReferenceDeckStatus, "reference_deck_status"), nullable=False
    )
    parse_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    security_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))

    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])


class NarrativeProfile(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "narrative_profiles"
    __table_args__ = (Index("ix_narrative_profiles_workspace_name", "workspace_id", "name"),)

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])
    visual_style_profiles: Mapped[list["VisualStyleProfile"]] = relationship(
        back_populates="narrative_profile"
    )


class VisualStyleProfile(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "style_profiles"
    __table_args__ = (Index("ix_style_profiles_workspace_status", "workspace_id", "status"),)

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reference_versions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    status: Mapped[StyleProfileStatus] = mapped_column(
        enum_column(StyleProfileStatus, "style_profile_status"), nullable=False
    )
    visual_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    narrative_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    narrative_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "narrative_profiles.id",
            name="fk_style_profiles_narrative_profile_id_narrative_profiles",
            ondelete="SET NULL",
        ),
        index=True,
    )
    palette: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    typography: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    layout_grammar: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    density: Mapped[str] = mapped_column(
        String(16), nullable=False, default="medium", server_default="medium"
    )
    shape_language: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    logo_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    confidence_notes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    conflict_notes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])
    confirmed_by: Mapped["User | None"] = relationship(foreign_keys=[confirmed_by_id])
    narrative_profile: Mapped["NarrativeProfile | None"] = relationship(
        back_populates="visual_style_profiles"
    )
    presentations: Mapped[list["Presentation"]] = relationship(back_populates="style_profile")


class Presentation(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "presentation_runs"
    __table_args__ = (Index("ix_presentation_solution_status", "solution_run_id", "status"),)

    solution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="RESTRICT"), nullable=False
    )
    solution_version: Mapped[int] = mapped_column(Integer, nullable=False)
    style_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("style_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    style_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[PresentationStatus] = mapped_column(
        enum_column(PresentationStatus, "presentation_status"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="balanced")
    audience: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    requested_outputs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    locked_block_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    upstream_trust_version: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    solution_run: Mapped["SolutionRun"] = relationship()
    style_profile: Mapped["VisualStyleProfile"] = relationship(back_populates="presentations")
    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])
    input_snapshots: Mapped[list["PresentationInputSnapshot"]] = relationship(
        back_populates="presentation", cascade="all, delete-orphan"
    )
    html_artifacts: Mapped[list["HtmlArtifact"]] = relationship(
        back_populates="presentation", cascade="all, delete-orphan"
    )


class PresentationInputSnapshot(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "presentation_input_snapshots"
    __table_args__ = (
        UniqueConstraint("presentation_id", "version", name="uq_presentation_snapshot_version"),
    )

    presentation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("presentation_runs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="presentation-input-v1"
    )
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    presentation: Mapped["Presentation"] = relationship(back_populates="input_snapshots")


class HtmlArtifact(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "html_artifacts"
    __table_args__ = (
        UniqueConstraint("presentation_id", "version", name="uq_html_artifact_version"),
    )

    presentation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("presentation_runs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    css: Mapped[str] = mapped_column(Text, nullable=False)
    assets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    render_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )
    upstream_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="released", server_default="released"
    )
    artifact_paths: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    preview_screenshot_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    html_object_key: Mapped[str | None] = mapped_column(String(1000))
    pdf_object_key: Mapped[str | None] = mapped_column(String(1000))

    presentation: Mapped["Presentation"] = relationship(back_populates="html_artifacts")


# Compatibility aliases for the V1 integration baseline. New code should use the
# contract-aligned names while existing services can migrate independently.
StyleProfile = VisualStyleProfile
PresentationRun = Presentation


class ExportArtifact(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "export_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "presentation_id", "html_artifact_id", "export_type", name="uq_export_artifact"
        ),
    )

    presentation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("presentation_runs.id", ondelete="CASCADE"), nullable=False
    )
    html_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("html_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    export_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[ExportStatus] = mapped_column(
        enum_column(ExportStatus, "export_artifact_status"), nullable=False
    )
    provider_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_workspace_status", "workspace_id", "status"),
        Index("ix_jobs_target", "target_id", "type"),
    )

    type: Mapped[JobType] = mapped_column(enum_column(JobType, "job_type"), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[ProcessStatus] = mapped_column(
        enum_column(ProcessStatus, "job_status"),
        nullable=False,
        default=ProcessStatus.PENDING,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_idempotency_records_workspace_key",
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class InvitationRedemption(Base):
    __tablename__ = "invitation_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_id_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "review_records"
    __table_args__ = (Index("ix_review_records_asset", "asset_type", "asset_id"),)

    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AIRunRecord(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "ai_run_records"
    __table_args__ = (
        Index("ix_ai_run_records_trace", "trace_id", "created_at"),
        Index("ix_ai_run_records_target", "target_type", "target_id"),
    )

    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ProcessStatus] = mapped_column(
        enum_column(ProcessStatus, "ai_run_status"), nullable=False
    )
    model_version: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str | None] = mapped_column(String(128))
    embedding_version: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ResearchTask(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        Index("ix_research_tasks_workspace_status", "workspace_id", "status"),
        Index("ix_research_tasks_profile_updated", "customer_profile_id", "updated_at"),
    )

    customer_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    completion_conditions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ResearchTaskStatus] = mapped_column(
        enum_column(ResearchTaskStatus, "research_task_status"),
        nullable=False,
        default=ResearchTaskStatus.QUEUED,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    conversation_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    evidence_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    research_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    routes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    audit: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    knowledge_gaps: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expert_questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    research_document_url: Mapped[str | None] = mapped_column(String(2000))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_reason: Mapped[str | None] = mapped_column(String(1000))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchStep(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "research_steps"
    __table_args__ = (
        UniqueConstraint("research_task_id", "sequence", name="uq_research_steps_task_sequence"),
        Index("ix_research_steps_task_status", "research_task_id", "status"),
    )

    research_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ProcessStatus] = mapped_column(
        enum_column(ProcessStatus, "research_step_status"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExpertContribution(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "expert_contributions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_id", "role", name="uq_expert_contributions_user_source_role"
        ),
        Index("ix_expert_contributions_source", "source_id", "updated_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ContributionRole] = mapped_column(
        enum_column(ContributionRole, "contribution_role"), nullable=False
    )
    asset_type: Mapped[str | None] = mapped_column(String(32))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class ExpertCollaboration(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "expert_collaborations"
    __table_args__ = (
        Index("ix_expert_collaborations_task_status", "research_task_id", "status"),
        Index(
            "uq_expert_collaborations_active_task",
            "workspace_id",
            "research_task_id",
            unique=True,
            postgresql_where=text(
                "is_deleted = false AND status IN "
                "('draft', 'awaiting_confirmation', 'group_created', 'sent')"
            ),
        ),
    )

    research_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[CollaborationStatus] = mapped_column(
        enum_column(CollaborationStatus, "expert_collaboration_status"),
        nullable=False,
        default=CollaborationStatus.DRAFT,
    )
    group_name: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_records: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    selected_expert_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_bundle: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feishu_document_id: Mapped[str | None] = mapped_column(String(256))
    feishu_document_url: Mapped[str | None] = mapped_column(String(2000))
    feishu_group_id: Mapped[str | None] = mapped_column(String(256))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExpertReply(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "expert_replies"
    __table_args__ = (
        UniqueConstraint("feishu_message_id", name="uq_expert_replies_message"),
        Index("ix_expert_replies_task", "research_task_id", "created_at"),
    )

    research_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    collaboration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expert_collaborations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    author_id: Mapped[str] = mapped_column(String(128), nullable=False)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    feishu_message_id: Mapped[str] = mapped_column(String(256), nullable=False)
    message_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[ExpertQuestionStatus] = mapped_column(
        enum_column(ExpertQuestionStatus, "expert_reply_status"),
        nullable=False,
        default=ExpertQuestionStatus.ANSWERED,
    )
    adopted_experience_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiences.id", ondelete="SET NULL")
    )


class SearchRun(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "search_runs"
    __table_args__ = (Index("ix_search_runs_workspace_status", "workspace_id", "status"),)

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    status: Mapped[SearchRunStatus] = mapped_column(
        enum_column(SearchRunStatus, "search_run_status"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RawArtifact(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "raw_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "artifact_key", name="uq_raw_artifact_key"),
        Index("ix_raw_artifacts_run_captured", "search_run_id", "captured_at"),
        Index("ix_raw_artifacts_content_sha256", "workspace_id", "content_sha256"),
    )

    search_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_runs.id", ondelete="RESTRICT")
    )
    artifact_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[RawArtifactKind] = mapped_column(
        enum_column(RawArtifactKind, "raw_artifact_kind"), nullable=False
    )
    status: Mapped[RawArtifactStatus] = mapped_column(
        enum_column(RawArtifactStatus, "raw_artifact_status"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    normalized_url: Mapped[str | None] = mapped_column(String(2000))
    source_filename: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(2000))
    text_content: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    security_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))


class IntelligenceItem(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "intelligence_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "fingerprint", name="uq_intelligence_item_fingerprint"),
        Index("ix_intelligence_items_run_created", "search_run_id", "created_at"),
    )

    search_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_runs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    freshness: Mapped[IntelligenceFreshness] = mapped_column(
        enum_column(IntelligenceFreshness, "intelligence_freshness"), nullable=False
    )
    review_status: Mapped[IntelligenceReviewStatus] = mapped_column(
        enum_column(IntelligenceReviewStatus, "intelligence_review_status"), nullable=False
    )
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class IntelligenceItemArtifactLink(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "intelligence_item_artifact_links"
    __table_args__ = (
        UniqueConstraint(
            "intelligence_item_id",
            "raw_artifact_id",
            name="uq_intelligence_item_artifact_link",
        ),
        Index("ix_intelligence_item_artifacts_item", "intelligence_item_id", "created_at"),
    )

    intelligence_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intelligence_items.id", ondelete="CASCADE"), nullable=False
    )
    raw_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    relation_type: Mapped[ArtifactRelationType] = mapped_column(
        enum_column(ArtifactRelationType, "artifact_relation_type"), nullable=False
    )
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class IntelligenceSnapshot(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "intelligence_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "fingerprint", name="uq_intelligence_snapshot_fingerprint"
        ),
    )

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    item_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v2.0")


class ProfileIntelligenceProposal(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "profile_intelligence_proposals"
    __table_args__ = (Index("ix_profile_intelligence_profile", "profile_id", "status"),)

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_profiles.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    proposed_patch: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus, "profile_intelligence_proposal_status"), nullable=False
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))


class TenderDocument(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "tender_documents"

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    customer_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_profiles.id", ondelete="SET NULL")
    )
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_artifacts.id", ondelete="RESTRICT")
    )
    source_filename: Mapped[str | None] = mapped_column(String(500))
    source_mime_type: Mapped[str | None] = mapped_column(String(128))
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")


class TenderParseVersion(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "tender_parse_versions"
    __table_args__ = (
        UniqueConstraint("tender_id", "version", name="uq_tender_parse_version"),
        Index("ix_tender_parse_versions_tender", "tender_id", "version"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=False
    )
    raw_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    document_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[TenderParseStatus] = mapped_column(
        enum_column(TenderParseStatus, "tender_parse_status"), nullable=False
    )
    document_ir: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(1000))


class TenderRequirement(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "tender_requirements"
    __table_args__ = (
        UniqueConstraint("tender_id", "sequence", name="uq_tender_requirement_sequence"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=False
    )
    parse_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_parse_versions.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acceptance_condition: Mapped[str | None] = mapped_column(Text)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ambiguities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source_location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[TenderRequirementStatus] = mapped_column(
        enum_column(TenderRequirementStatus, "tender_requirement_status"), nullable=False
    )
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenderRequirementVersion(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "tender_requirement_versions"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version", name="uq_tender_requirement_version"),
        Index("ix_tender_requirement_versions_requirement", "requirement_id", "version"),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_requirements.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ResponseMatrix(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "response_matrices"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ResponseMatrixItem(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "response_matrix_items"
    __table_args__ = (Index("ix_response_matrix_items_matrix", "matrix_id", "created_at"),)

    matrix_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("response_matrices.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tender_requirements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_status: Mapped[ResponseEvidenceStatus] = mapped_column(
        enum_column(ResponseEvidenceStatus, "response_evidence_status"), nullable=False
    )
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    review_note: Mapped[str | None] = mapped_column(String(1000))


class RehearsalSession(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "rehearsal_sessions"
    __table_args__ = (Index("ix_rehearsal_workspace_status", "workspace_id", "status"),)

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    customer_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    solution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("solution_runs.id", ondelete="SET NULL")
    )
    research_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_tasks.id", ondelete="SET NULL")
    )
    intelligence_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intelligence_snapshots.id", ondelete="SET NULL")
    )
    response_matrix_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("response_matrices.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False)
    focus_areas: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[RehearsalStatus] = mapped_column(
        enum_column(RehearsalStatus, "rehearsal_status"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    current_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))


class RehearsalTurn(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "rehearsal_turns"
    __table_args__ = (
        UniqueConstraint("rehearsal_id", "sequence", name="uq_rehearsal_turn_sequence"),
    )

    rehearsal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rehearsal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_question: Mapped[str] = mapped_column(Text, nullable=False)
    employee_answer: Mapped[str | None] = mapped_column(Text)
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class RehearsalReport(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "rehearsal_reports"
    __table_args__ = (UniqueConstraint("rehearsal_id", name="uq_rehearsal_report"),)

    rehearsal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rehearsal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    report_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v2.0")
