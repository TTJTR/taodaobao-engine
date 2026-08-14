import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.errors import ErrorCode
from app.db.database import get_session_factory
from app.db.models import (
    ExportArtifact,
    ExportStatus,
    HtmlArtifact,
    PresentationInputSnapshot,
    PresentationRun,
    PresentationStatus,
    ReferenceDeck,
    ReferenceDeckStatus,
    StyleProfile,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.integrations.presentation import PresentationProvider, get_presentation_provider
from app.schemas.presentation import PositionedPresentationSpec, VisualStyleProfileData
from app.services.pptx_renderer import PPTXRenderer


async def run_presentation_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    kind: str,
    target_id: uuid.UUID,
    *,
    provider: PresentationProvider | None = None,
) -> None:
    provider = provider or get_presentation_provider()
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.workspace_id == workspace_id,
                WorkflowTask.is_deleted.is_(False),
            )
        )
        if task is None:
            return
        try:
            if kind == "reference_parse":
                await _parse_reference(session, workspace_id, target_id, task, provider)
            elif kind == "style_profile":
                await _generate_style(session, workspace_id, target_id, task, provider)
            elif kind == "presentation_render":
                await _render_presentation(session, workspace_id, target_id, task, provider)
            elif kind == "presentation_export":
                await _export_presentation(session, workspace_id, target_id, task, provider)
            else:
                raise ValueError(f"unsupported workflow task kind: {kind}")
            task.status = WorkflowTaskStatus.COMPLETED
            task.stage = "completed"
            task.finished_at = datetime.now(UTC)
            task.lease_owner = None
            task.lease_expires_at = None
            await session.commit()
        except Exception as exc:
            await session.rollback()
            task = await session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.id == task_id,
                    WorkflowTask.workspace_id == workspace_id,
                )
            )
            if task is None:
                return
            task.status = WorkflowTaskStatus.FAILED
            task.stage = "failed"
            task.error_code = _error_code(kind, exc).value
            task.error_summary = type(exc).__name__
            task.finished_at = datetime.now(UTC)
            task.lease_owner = None
            task.lease_expires_at = None
            await _mark_target_failed(session, workspace_id, kind, target_id, task.error_code)
            await session.commit()


async def _parse_reference(session, workspace_id, target_id, task, provider) -> None:
    deck = await _entity(session, ReferenceDeck, workspace_id, target_id)
    deck.status = ReferenceDeckStatus.PARSING
    task.stage = "parsing"
    _renew_lease(task)
    await session.commit()
    result = await provider.parse_reference(
        {
            "deck_id": str(deck.id),
            "storage_key": deck.storage_key,
            "file_hash": deck.file_hash,
            "mime_type": deck.mime_type,
            "size_bytes": deck.size_bytes,
        }
    )
    security = result.get("security_report") or {}
    if not security.get("safe"):
        raise PermissionError("reference deck was not proven safe")
    deck.parse_result = result
    deck.security_report = security
    deck.page_count = int(result.get("page_count") or 0)
    deck.status = ReferenceDeckStatus.PARSED


async def _generate_style(session, workspace_id, target_id, task, provider) -> None:
    profile = await _entity(session, StyleProfile, workspace_id, target_id)
    task.stage = "profile_draft"
    _renew_lease(task)
    await session.commit()
    deck_ids = [uuid.UUID(item["deck_id"]) for item in profile.reference_versions]
    decks = list(
        (
            await session.scalars(
                select(ReferenceDeck).where(
                    ReferenceDeck.workspace_id == workspace_id,
                    ReferenceDeck.id.in_(deck_ids),
                    ReferenceDeck.is_deleted.is_(False),
                )
            )
        ).all()
    )
    result = await provider.generate_style(
        [
            {
                "deck_id": str(item.id),
                "version": item.version,
                "parse_result": item.parse_result,
            }
            for item in decks
        ]
    )
    profile.visual_json = result["visual_json"]
    profile.narrative_json = {
        **result["narrative_json"],
        "provider_mode": result.get("provider_mode", provider.mode),
    }
    profile.conflict_notes = list(result.get("conflict_notes") or [])


async def _render_presentation(session, workspace_id, target_id, task, provider) -> None:
    run = await _entity(session, PresentationRun, workspace_id, target_id)
    profile = await _entity(session, StyleProfile, workspace_id, run.style_profile_id)
    snapshot = await session.scalar(
        select(PresentationInputSnapshot)
        .where(
            PresentationInputSnapshot.workspace_id == workspace_id,
            PresentationInputSnapshot.presentation_id == run.id,
            PresentationInputSnapshot.is_deleted.is_(False),
        )
        .order_by(PresentationInputSnapshot.version.desc())
        .limit(1)
    )
    if snapshot is None:
        raise RuntimeError("presentation input snapshot is missing")
    input_data = dict(snapshot.snapshot_data)
    if task.payload.get("spec_override"):
        input_data["spec_override"] = task.payload["spec_override"]
    run.status = PresentationStatus.PLANNING
    task.stage = "planning"
    await session.commit()
    run.status = PresentationStatus.RENDERING
    task.stage = "rendering"
    _renew_lease(task, seconds=600 if input_data.get("render_mode") == "interactive" else 180)
    await session.commit()
    result = await provider.render(
        input_data,
        {
            "profile_id": str(profile.id),
            "version": profile.version,
            "visual_json": profile.visual_json,
            "narrative_json": profile.narrative_json,
        },
    )
    run.status = PresentationStatus.VALIDATING
    task.stage = "validating"
    await session.commit()
    artifact_version = (
        int(
            await session.scalar(
                select(HtmlArtifact.version)
                .where(
                    HtmlArtifact.workspace_id == workspace_id,
                    HtmlArtifact.presentation_id == run.id,
                    HtmlArtifact.is_deleted.is_(False),
                )
                .order_by(HtmlArtifact.version.desc())
                .limit(1)
            )
            or 0
        )
        + 1
    )
    report = result.get("render_report") or {}
    session.add(
        HtmlArtifact(
            workspace_id=workspace_id,
            presentation_id=run.id,
            version=artifact_version,
            html=result["html"],
            css=result["css"],
            assets=result.get("assets") or [],
            render_report=report,
            provider_mode=result.get("provider_mode", provider.mode),
        )
    )
    run.spec = result["spec"]
    run.status = (
        PresentationStatus.READY if report.get("passed") else PresentationStatus.NEEDS_REVIEW
    )
    run.completed_at = datetime.now(UTC)


async def _export_presentation(session, workspace_id, target_id, task, provider) -> None:
    export = await _entity(session, ExportArtifact, workspace_id, target_id)
    artifact = await _entity(session, HtmlArtifact, workspace_id, export.html_artifact_id)
    run = await _entity(session, PresentationRun, workspace_id, export.presentation_id)
    export.status = ExportStatus.RUNNING
    task.stage = "exporting"
    _renew_lease(task)
    await session.commit()
    if export.export_type == "pptx":
        profile = await _entity(session, StyleProfile, workspace_id, run.style_profile_id)
        spec = PositionedPresentationSpec.model_validate(run.spec)
        style = VisualStyleProfileData.model_validate(profile.visual_json)
        rendered = await PPTXRenderer().render(spec, style, artifact_id=str(export.id))
        result = {
            "object_key": str(rendered.path),
            "provider_mode": "deterministic-pptx-v1",
        }
    else:
        result = await provider.export(
            {
                "artifact_id": str(artifact.id),
                "html": artifact.html,
                "css": artifact.css,
                "assets": artifact.assets,
                "render_report": artifact.render_report,
            },
            export.export_type,
        )
    export.object_key = result["object_key"]
    export.provider_mode = result.get("provider_mode", provider.mode)
    export.status = ExportStatus.READY


async def _mark_target_failed(session, workspace_id, kind, target_id, error_code) -> None:
    if kind == "reference_parse":
        item = await _entity(session, ReferenceDeck, workspace_id, target_id)
        item.status = ReferenceDeckStatus.FAILED
        item.error_code = error_code
    elif kind == "presentation_render":
        item = await _entity(session, PresentationRun, workspace_id, target_id)
        item.status = PresentationStatus.FAILED
        item.error_code = error_code
    elif kind == "presentation_export":
        item = await _entity(session, ExportArtifact, workspace_id, target_id)
        item.status = ExportStatus.FAILED
        item.error_code = error_code


async def _entity(session, model, workspace_id, entity_id):
    item = await session.scalar(
        select(model).where(
            model.id == entity_id,
            model.workspace_id == workspace_id,
            model.is_deleted.is_(False),
        )
    )
    if item is None:
        raise RuntimeError(f"{model.__name__} is missing")
    return item


def _error_code(kind: str, exc: Exception) -> ErrorCode:
    if kind == "reference_parse" and isinstance(exc, PermissionError):
        return ErrorCode.REFERENCE_DECK_UNSAFE
    return ErrorCode.PRESENTATION_RENDER_FAILED


def _renew_lease(task: WorkflowTask, *, seconds: int = 180) -> None:
    now = datetime.now(UTC)
    task.heartbeat_at = now
    task.lease_expires_at = now + timedelta(seconds=seconds)
