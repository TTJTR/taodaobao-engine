import asyncio
import uuid
from datetime import UTC, datetime

from app.ai.embedding import content_fingerprint
from app.contracts.ai import AIEngine
from app.core.errors import ErrorCode
from app.db.database import get_session_factory
from app.db.models import (
    Capability,
    ContributionRole,
    ProcessStatus,
    ProfileStatus,
    ReviewStatus,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
)
from app.db.repositories import (
    CapabilityRepository,
    CustomerProfileRepository,
    ExperienceRepository,
    ExpertContributionRepository,
    JobRepository,
    SourceRepository,
    UserRepository,
)
from app.integrations import FeishuAdapter
from app.integrations.protocols import FeishuCollaborator
from app.services.ai_run_service import persist_last_ai_run


async def run_source_job(
    job_id: uuid.UUID,
    source_id: uuid.UUID,
    workspace_id: uuid.UUID,
    feishu_adapter: FeishuAdapter | None,
    ai_engine: AIEngine,
    access_token: str | None = None,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        jobs = JobRepository(session, workspace_id)
        sources = SourceRepository(session, workspace_id)
        job = await jobs.get(job_id)
        source = await sources.get(source_id)
        if job is None or source is None:
            return

        try:
            await _set_stage(session, job, source, "fetching", SourceStatus.FETCHING)
            collaborators: tuple[FeishuCollaborator, ...] = ()
            if source.type == SourceType.FEISHU_DOC:
                if feishu_adapter is None:
                    raise RuntimeError("Feishu adapter is required for a Feishu document source")
                if access_token is None:
                    document = await feishu_adapter.fetch_document(source.source_url or "")
                else:
                    document = await feishu_adapter.fetch_document(
                        source.source_url or "", access_token
                    )
                collaborators = await _resolve_collaborator_names(
                    session,
                    workspace_id,
                    document.collaborators,
                )
                source.title = document.title
                source.content = document.content
                visible_authors = [item.name for item in collaborators if item.is_owner]
                source.author = (
                    "、".join(dict.fromkeys(visible_authors))
                    or document.author
                )
                source.source_url = document.source_url
                source.source_updated_at = _parse_datetime(document.source_updated_at)
                source.synced_at = datetime.now(UTC)
                await session.commit()

            await _set_stage(session, job, source, "parsing", SourceStatus.PARSING)
            await asyncio.sleep(0)
            raw_text = (source.content or "").strip()
            if not raw_text:
                raise ValueError("source content is empty")
            source.content_fingerprint = content_fingerprint(raw_text)
            await _set_stage(session, job, source, "extracting", SourceStatus.EXTRACTING)
            await asyncio.sleep(0)

            if source.purpose == SourcePurpose.EXPERIENCE:
                draft = await ai_engine.extract_experience(raw_text, str(source.id))
                persist_last_ai_run(
                    session,
                    workspace_id,
                    ai_engine,
                    target_type="source",
                    target_id=source.id,
                    input_summary={"purpose": source.purpose.value, "characters": len(raw_text)},
                )
                draft = _normalize_experience(draft)
                experiences = ExperienceRepository(session, workspace_id)
                asset = await experiences.get_by_source(source.id)
                if asset is None:
                    asset = await experiences.create(
                        source_id=source.id,
                        data=draft,
                        review_status=ReviewStatus.PENDING_REVIEW,
                        embedding_ready=False,
                    )
                else:
                    asset.data = draft
                    asset.review_status = ReviewStatus.PENDING_REVIEW
                    asset.embedding_ready = False
            elif source.purpose == SourcePurpose.CAPABILITY:
                drafts = await ai_engine.extract_capabilities(raw_text, str(source.id))
                persist_last_ai_run(
                    session,
                    workspace_id,
                    ai_engine,
                    target_type="source",
                    target_id=source.id,
                    input_summary={"purpose": source.purpose.value, "characters": len(raw_text)},
                )
                drafts = [_normalize_capability(draft) for draft in drafts]
                capabilities = CapabilityRepository(session, workspace_id)
                existing = await capabilities.list_by_source(source.id)
                for index, draft in enumerate(drafts):
                    if index < len(existing):
                        asset = existing[index]
                        asset.data = draft
                        asset.review_status = ReviewStatus.PENDING_REVIEW
                        asset.embedding_ready = False
                    else:
                        session.add(
                            Capability(
                                workspace_id=workspace_id,
                                source_id=source.id,
                                data=draft,
                                review_status=ReviewStatus.PENDING_REVIEW,
                                embedding_ready=False,
                            )
                        )
                for stale in existing[len(drafts) :]:
                    stale.is_deleted = True

            await _record_source_contributors(session, workspace_id, source, collaborators)

            source.status = SourceStatus.PENDING_REVIEW
            job.status = ProcessStatus.COMPLETED
            job.stage = "pending_review"
            job.finished_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            job = await jobs.get(job_id)
            source = await sources.get(source_id)
            if job is None or source is None:
                return
            persist_last_ai_run(
                session,
                workspace_id,
                ai_engine,
                target_type="source",
                target_id=source.id,
                input_summary={"stage": job.stage, "failed": True},
            )
            source.status = SourceStatus.FAILED
            upstream_status = getattr(getattr(exc, "response", None), "status_code", None)
            if upstream_status == 403:
                source.freshness_status = SourceFreshness.PERMISSION_DENIED
            elif upstream_status == 404:
                source.freshness_status = SourceFreshness.DELETED
            job.status = ProcessStatus.FAILED
            job.stage = "failed"
            job.error_code = ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE.value
            job.error_summary = str(exc)[:1000]
            job.retry_count += 1
            job.finished_at = datetime.now(UTC)
            await session.commit()


async def run_profile_job(
    job_id: uuid.UUID,
    profile_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ai_engine: AIEngine,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        jobs = JobRepository(session, workspace_id)
        profiles = CustomerProfileRepository(session, workspace_id)
        sources = SourceRepository(session, workspace_id)
        job = await jobs.get(job_id)
        profile = await profiles.get(profile_id)
        if job is None or profile is None:
            return
        try:
            job.status = ProcessStatus.RUNNING
            job.stage = "collecting_sources"
            await session.commit()
            source_items = [
                source
                for source_id in profile.source_ids
                if (source := await sources.get(source_id)) is not None and source.content
            ]
            supplemental = str(profile.profile.get("supplemental_text") or "").strip()
            parts = [source.content or "" for source in source_items]
            if supplemental:
                parts.append(supplemental)
            if not parts:
                raise ValueError("profile generation has no usable source content")
            job.stage = "extracting"
            await session.commit()
            source_ids = [str(source.id) for source in source_items]
            if not source_ids:
                source_ids = [f"profile-supplement:{profile.id}"]
            draft = await ai_engine.extract_profile("\n\n".join(parts), source_ids)
            persist_last_ai_run(
                session,
                workspace_id,
                ai_engine,
                target_type="customer_profile",
                target_id=profile.id,
                input_summary={
                    "source_count": len(source_items),
                    "has_supplement": bool(supplemental),
                },
            )
            profile.customer_name = str(draft.pop("customer_name", profile.customer_name))
            current_problems = draft.pop("current_problems", [])
            draft["current_problem"] = "；".join(_as_list(current_problems)) or None
            draft.pop("profile_status", None)
            profile.profile = draft
            profile.status = ProfileStatus.PENDING_CONFIRMATION
            profile.confirmed_by_id = None
            profile.confirmed_at = None
            job.status = ProcessStatus.COMPLETED
            job.stage = "pending_confirmation"
            job.finished_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            job = await jobs.get(job_id)
            if job is not None:
                persist_last_ai_run(
                    session,
                    workspace_id,
                    ai_engine,
                    target_type="customer_profile",
                    target_id=profile_id,
                    input_summary={"stage": job.stage, "failed": True},
                )
                job.status = ProcessStatus.FAILED
                job.stage = "failed"
                job.error_code = ErrorCode.MODEL_TEMPORARILY_UNAVAILABLE.value
                job.error_summary = str(exc)[:1000]
                job.retry_count += 1
                job.finished_at = datetime.now(UTC)
                await session.commit()


async def _set_stage(session, job, source, stage: str, source_status: SourceStatus) -> None:
    job.status = ProcessStatus.RUNNING
    job.stage = stage
    source.status = source_status
    await session.commit()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _normalize_experience(draft: dict) -> dict:
    normalized = dict(draft)
    for field in ("prerequisites", "risks", "applicable_conditions"):
        normalized[field] = _as_list(normalized.get(field))
    return normalized


def _normalize_capability(draft: dict) -> dict:
    normalized = dict(draft)
    for field in ("inputs", "outputs", "prerequisites", "limitations", "tags"):
        normalized[field] = _as_list(normalized.get(field))
    return normalized


async def _record_source_contributors(
    session,
    workspace_id,
    source,
    collaborators: tuple[FeishuCollaborator, ...] = (),
) -> None:
    users = UserRepository(session, workspace_id)
    records: list[tuple[uuid.UUID, ContributionRole]] = []
    for collaborator in collaborators:
        user = await users.get_by_feishu_user_id(collaborator.feishu_user_id)
        if user is None:
            user = await users.create(
                feishu_user_id=collaborator.feishu_user_id,
                name=collaborator.name,
                avatar=None,
            )
        role = (
            ContributionRole.SOURCE_AUTHOR
            if collaborator.is_owner
            else ContributionRole.ASSET_CONTRIBUTOR
        )
        records.append((user.id, role))
    if not records and source.author:
        author = await users.get_by_name(source.author)
        if author is not None:
            records.append((author.id, ContributionRole.SOURCE_AUTHOR))
    if not records:
        records.append((source.imported_by_id, ContributionRole.ASSET_CONTRIBUTOR))

    repository = ExpertContributionRepository(session, workspace_id)
    values = {
        "asset_type": source.purpose.value,
        "asset_id": None,
        "title": source.title,
        "tags": source.tags,
    }
    for user_id, role in dict.fromkeys(records):
        existing = await repository.get_for_user_source_role(user_id, source.id, role)
        if existing is None:
            await repository.create(user_id=user_id, source_id=source.id, role=role, **values)
        else:
            await repository.update(existing, **values)


async def _resolve_collaborator_names(
    session,
    workspace_id,
    collaborators: tuple[FeishuCollaborator, ...],
) -> tuple[FeishuCollaborator, ...]:
    """Prefer previously verified workspace names over Feishu's restricted placeholders."""
    users = UserRepository(session, workspace_id)
    resolved: list[FeishuCollaborator] = []
    for collaborator in collaborators:
        known_user = await users.get_by_feishu_user_id(collaborator.feishu_user_id)
        name = collaborator.name
        if known_user is not None and "姓名受限" in name:
            name = known_user.name
        resolved.append(
            FeishuCollaborator(
                feishu_user_id=collaborator.feishu_user_id,
                name=name,
                permission=collaborator.permission,
                is_owner=collaborator.is_owner,
            )
        )
    return tuple(resolved)
