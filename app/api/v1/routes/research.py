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
from app.db.models import ResearchTaskStatus
from app.schemas.v1 import (
    CancelResearchTaskRequest,
    CreateResearchTaskRequest,
    ResearchContextRead,
    ResearchStepRead,
    ResearchTaskDetail,
    ResearchTaskRead,
)
from app.services.research_pipeline import run_research_pipeline
from app.services.research_service import ResearchTaskService

router = APIRouter(route_class=IdempotencyRoute)


def serialize_task(task) -> dict:
    return ResearchTaskRead.model_validate(task).model_dump(mode="json")


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_research_task(
    payload: CreateResearchTaskRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    embedding_provider: EmbeddingProviderDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    task = await ResearchTaskService(session, workspace_id, current_user.id).create(
        customer_profile_id=payload.customer_profile_id,
        session_id=payload.session_id,
        title=payload.title,
        question=payload.question,
        completion_conditions=payload.completion_conditions,
        intelligence_snapshot_id=payload.intelligence_snapshot_id,
        response_matrix_id=payload.response_matrix_id,
    )
    background_tasks.add_task(
        run_research_pipeline,
        task.id,
        workspace_id,
        current_user.id,
        ai_engine,
        embedding_provider,
    )
    return success_response(request, serialize_task(task))


@router.get("")
async def list_research_tasks(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    task_status: Annotated[ResearchTaskStatus | None, Query(alias="status")] = None,
    profile_id: uuid.UUID | None = None,
) -> dict[str, object]:
    items, total = await ResearchTaskService(session, workspace_id, current_user.id).list_tasks(
        page=page,
        page_size=page_size,
        status=task_status,
        profile_id=profile_id,
    )
    return success_response(
        request,
        {
            "items": [serialize_task(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{task_id}")
async def get_research_task(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    task, steps = await ResearchTaskService(session, workspace_id, current_user.id).detail(task_id)
    data = ResearchTaskDetail.model_validate(task).model_dump(mode="json")
    data["steps"] = [
        ResearchStepRead.model_validate(item).model_dump(mode="json") for item in steps
    ]
    return success_response(request, data)


@router.get("/{task_id}/context")
async def get_research_context(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    data = await ResearchTaskService(session, workspace_id, current_user.id).context(task_id)
    return success_response(
        request, ResearchContextRead.model_validate(data).model_dump(mode="json")
    )


@router.post("/{task_id}/cancel")
async def cancel_research_task(
    task_id: uuid.UUID,
    payload: CancelResearchTaskRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    task = await ResearchTaskService(session, workspace_id, current_user.id).cancel(
        task_id, payload.reason
    )
    return success_response(request, serialize_task(task))


@router.post("/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_research_task(
    task_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    embedding_provider: EmbeddingProviderDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    task = await ResearchTaskService(session, workspace_id, current_user.id).retry(task_id)
    background_tasks.add_task(
        run_research_pipeline,
        task.id,
        workspace_id,
        current_user.id,
        ai_engine,
        embedding_provider,
    )
    return success_response(request, serialize_task(task))
