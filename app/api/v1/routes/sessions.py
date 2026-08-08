import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from app.api.deps import (
    AIEngineDependency,
    CurrentUser,
    DatabaseSession,
    EmbeddingProviderDependency,
    WorkspaceId,
)
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.chat import CreateSessionRequest, CreateTurnRequest, MessageRead, SessionRead
from app.services.session_service import SessionService
from app.services.solution_pipeline import run_solution_pipeline

router = APIRouter(route_class=IdempotencyRoute)


def serialize_session(chat) -> dict:
    return SessionRead.model_validate(chat).model_dump(mode="json")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreateSessionRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    chat = await SessionService(session, workspace_id, current_user.id).create(
        payload.customer_profile_id, payload.title
    )
    return success_response(request, serialize_session(chat))


@router.get("")
async def list_sessions(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    profile_id: uuid.UUID | None = None,
) -> dict[str, object]:
    items, total = await SessionService(session, workspace_id, current_user.id).list_sessions(
        page=page, page_size=page_size, profile_id=profile_id
    )
    return success_response(
        request,
        {
            "items": [serialize_session(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    chat, messages = await SessionService(session, workspace_id, current_user.id).get_detail(
        session_id
    )
    data = serialize_session(chat)
    data["messages"] = [
        MessageRead.model_validate(message).model_dump(mode="json") for message in messages
    ]
    return success_response(request, data)


@router.post("/{session_id}/turns", status_code=status.HTTP_202_ACCEPTED)
async def create_turn(
    session_id: uuid.UUID,
    payload: CreateTurnRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    embedding_provider: EmbeddingProviderDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    message, run = await SessionService(session, workspace_id, current_user.id).create_turn(
        session_id, payload.content
    )
    background_tasks.add_task(
        run_solution_pipeline, run.id, workspace_id, ai_engine, embedding_provider
    )
    return success_response(
        request,
        {
            "run_id": str(run.id),
            "message_id": str(message.id),
            "status": run.status.value,
            "poll_after_ms": 1000,
        },
    )
