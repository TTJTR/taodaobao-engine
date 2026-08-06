import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
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
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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
    imported_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    type: Mapped[SourceType] = mapped_column(
        enum_column(SourceType, "source_type"), nullable=False
    )
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

    customer_profile: Mapped[CustomerProfile | None] = relationship(back_populates="sources")
    imported_by: Mapped[User] = relationship(
        back_populates="imported_sources", foreign_keys=[imported_by_id]
    )
    experience: Mapped["Experience | None"] = relationship(
        back_populates="source", uselist=False
    )
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

    source: Mapped[Source] = relationship(back_populates="experience")


class Capability(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "capabilities"
    __table_args__ = (
        Index("ix_capabilities_workspace_review", "workspace_id", "review_status"),
    )

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

    source: Mapped[Source] = relationship(back_populates="capabilities")


class Session(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_profile_updated", "customer_profile_id", "updated_at"),
    )

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

    session: Mapped[Session] = relationship(
        back_populates="messages", foreign_keys=[session_id]
    )
    solution_run: Mapped["SolutionRun | None"] = relationship(
        back_populates="response_messages", foreign_keys=[solution_run_id]
    )


class SolutionRun(EntityMixin, WorkspaceMixin, Base):
    __tablename__ = "solution_runs"
    __table_args__ = (
        Index("ix_solution_runs_session_created", "session_id", "created_at"),
    )

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

    session: Mapped[Session] = relationship(
        back_populates="solution_runs", foreign_keys=[session_id]
    )
    request_message: Mapped[Message] = relationship(foreign_keys=[request_message_id])
    response_messages: Mapped[list[Message]] = relationship(
        back_populates="solution_run", foreign_keys="Message.solution_run_id"
    )


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
