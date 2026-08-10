import uuid

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentUser, DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.presentations import (
    ConfirmStyleProfileRequest,
    CreateReferenceDeckRequest,
    ExportPresentationRequest,
    GenerateStyleProfileRequest,
    RegeneratePresentationRequest,
    UpdatePresentationBlockRequest,
    UpdateStyleProfileRequest,
)
from app.services.presentation_service import PresentationService

references_router = APIRouter(route_class=IdempotencyRoute)
styles_router = APIRouter(route_class=IdempotencyRoute)
presentations_router = APIRouter(route_class=IdempotencyRoute)


@references_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_reference_deck(
    payload: CreateReferenceDeckRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    deck = await PresentationService(session, workspace_id, current_user.id).create_reference(
        payload
    )
    return success_response(request, _deck_data(deck))


@references_router.get("/{deck_id}")
async def get_reference_deck(
    deck_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
) -> dict[str, object]:
    deck = await PresentationService(session, workspace_id, current_user.id).get_reference(deck_id)
    return success_response(request, _deck_data(deck))


@styles_router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_style_profile(
    payload: GenerateStyleProfileRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    profile = await PresentationService(session, workspace_id, current_user.id).generate_style(
        payload
    )
    return success_response(request, _style_data(profile))


@styles_router.get("/{profile_id}")
async def get_style_profile(
    profile_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
) -> dict[str, object]:
    profile = await PresentationService(session, workspace_id, current_user.id).get_style(
        profile_id
    )
    return success_response(request, _style_data(profile))


@styles_router.patch("/{profile_id}")
async def update_style_profile(
    profile_id: uuid.UUID,
    payload: UpdateStyleProfileRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    profile = await PresentationService(session, workspace_id, current_user.id).update_style(
        profile_id, payload
    )
    return success_response(request, _style_data(profile))


@styles_router.post("/{profile_id}/confirm")
async def confirm_style_profile(
    profile_id: uuid.UUID,
    payload: ConfirmStyleProfileRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    profile = await PresentationService(session, workspace_id, current_user.id).confirm_style(
        profile_id, payload.expected_version
    )
    return success_response(request, _style_data(profile))


@presentations_router.get("/{presentation_id}")
async def get_presentation(
    presentation_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
) -> dict[str, object]:
    item = await PresentationService(session, workspace_id, current_user.id).get_presentation(
        presentation_id
    )
    return success_response(request, item)


@presentations_router.patch("/{presentation_id}/blocks/{block_id}")
async def update_presentation_block(
    presentation_id: uuid.UUID,
    block_id: str,
    payload: UpdatePresentationBlockRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    run = await PresentationService(session, workspace_id, current_user.id).update_block(
        presentation_id, block_id, payload
    )
    return success_response(
        request,
        {"presentation_id": str(run.id), "status": run.status.value, "version": run.version},
    )


@presentations_router.post("/{presentation_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_presentation(
    presentation_id: uuid.UUID,
    payload: RegeneratePresentationRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    run = await PresentationService(session, workspace_id, current_user.id).regenerate(
        presentation_id, payload
    )
    return success_response(
        request,
        {"presentation_id": str(run.id), "status": run.status.value, "version": run.version},
    )


@presentations_router.post("/{presentation_id}/export", status_code=status.HTTP_202_ACCEPTED)
async def export_presentation(
    presentation_id: uuid.UUID,
    payload: ExportPresentationRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    item = await PresentationService(session, workspace_id, current_user.id).export(
        presentation_id, payload
    )
    return success_response(
        request,
        {
            "export_id": str(item.id),
            "presentation_id": str(item.presentation_id),
            "export_type": item.export_type,
            "status": item.status.value,
            "object_key": item.object_key,
            "provider_mode": item.provider_mode,
        },
    )


def _deck_data(deck) -> dict:
    return {
        "id": str(deck.id),
        "file_id": deck.file_id,
        "title": deck.title,
        "source_url": deck.source_url,
        "file_hash": deck.file_hash,
        "version": deck.version,
        "mime_type": deck.mime_type,
        "size_bytes": deck.size_bytes,
        "page_count": deck.page_count,
        "status": deck.status.value,
        "parse_result": deck.parse_result,
        "security_report": deck.security_report,
        "error_code": deck.error_code,
    }


def _style_data(profile) -> dict:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "version": profile.version,
        "reference_versions": profile.reference_versions,
        "status": profile.status.value,
        "visual_json": profile.visual_json,
        "narrative_json": profile.narrative_json,
        "conflict_notes": profile.conflict_notes,
        "confirmed_by_id": str(profile.confirmed_by_id) if profile.confirmed_by_id else None,
        "confirmed_at": profile.confirmed_at.isoformat() if profile.confirmed_at else None,
    }
