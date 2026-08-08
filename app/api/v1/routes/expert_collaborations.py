import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from app.api.deps import (
    AIEngineDependency,
    CurrentUser,
    DatabaseSession,
    EmbeddingProviderDependency,
    FeishuAdapterDependency,
    WorkspaceId,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.v1 import (
    AdoptExpertReplyRequest,
    CreateExpertCollaborationRequest,
    ExpertCollaborationRead,
    ExpertReplyEvent,
    ExpertReplyRead,
    UpdateExpertCollaborationRequest,
)
from app.services.expert_collaboration_service import (
    ExpertCollaborationService,
    resolve_collaboration_workspace,
)
from app.services.research_pipeline import run_research_pipeline

router = APIRouter(route_class=IdempotencyRoute)
events_router = APIRouter(route_class=IdempotencyRoute)


def serialize_collaboration(item) -> dict:
    return ExpertCollaborationRead.model_validate(item).model_dump(mode="json")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_expert_collaboration(
    payload: CreateExpertCollaborationRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    feishu: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    item = await ExpertCollaborationService(
        session, workspace_id, current_user.id, ai_engine, feishu
    ).create(payload.research_task_id)
    return success_response(request, serialize_collaboration(item))


@router.get("")
async def list_expert_collaborations(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    feishu: FeishuAdapterDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    items, total = await ExpertCollaborationService(
        session, workspace_id, current_user.id, ai_engine, feishu
    ).list_items(page, page_size)
    return success_response(
        request,
        {
            "items": [serialize_collaboration(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{collaboration_id}")
async def get_expert_collaboration(
    collaboration_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    feishu: FeishuAdapterDependency,
) -> dict[str, object]:
    item = await ExpertCollaborationService(
        session, workspace_id, current_user.id, ai_engine, feishu
    ).get(collaboration_id)
    return success_response(request, serialize_collaboration(item))


@router.patch("/{collaboration_id}")
async def update_expert_collaboration(
    collaboration_id: uuid.UUID,
    payload: UpdateExpertCollaborationRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    feishu: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    item = await ExpertCollaborationService(
        session, workspace_id, current_user.id, ai_engine, feishu
    ).update_draft(
        collaboration_id,
        group_name=payload.group_name,
        selected_expert_ids=payload.selected_expert_ids,
        questions=[question.model_dump(mode="json") for question in payload.questions],
    )
    return success_response(request, serialize_collaboration(item))


@router.post("/{collaboration_id}/confirm")
async def confirm_expert_collaboration(
    collaboration_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    feishu: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    item = await ExpertCollaborationService(
        session, workspace_id, current_user.id, ai_engine, feishu
    ).confirm(collaboration_id)
    return success_response(request, serialize_collaboration(item))


@events_router.post("/events/replies", status_code=status.HTTP_202_ACCEPTED)
async def receive_expert_reply(
    payload: ExpertReplyEvent,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    ai_engine: AIEngineDependency,
    embedding_provider: EmbeddingProviderDependency,
    feishu: FeishuAdapterDependency,
) -> dict[str, object]:
    if settings.feishu_mode == "live":
        configured = settings.feishu_verification_token or ""
        supplied = payload.verification_token or ""
        if not configured or not hmac.compare_digest(configured, supplied):
            raise AppError(ErrorCode.AUTH_REQUIRED, "飞书事件校验失败", status_code=401)
    workspace_id, user_id = await resolve_collaboration_workspace(session, payload.collaboration_id)
    reply, task = await ExpertCollaborationService(
        session, workspace_id, user_id, ai_engine, feishu
    ).record_reply(
        collaboration_id=payload.collaboration_id,
        question_id=payload.question_id,
        author_id=payload.author_id,
        author_name=payload.author_name,
        answer_text=payload.answer_text,
        feishu_message_id=payload.feishu_message_id,
        message_url=payload.message_url,
    )
    background_tasks.add_task(
        run_research_pipeline,
        task.id,
        workspace_id,
        user_id,
        ai_engine,
        embedding_provider,
    )
    return success_response(request, ExpertReplyRead.model_validate(reply).model_dump(mode="json"))


@router.post("/replies/{reply_id}/adopt")
async def adopt_expert_reply(
    reply_id: uuid.UUID,
    payload: AdoptExpertReplyRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    feishu: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    reply = await ExpertCollaborationService(
        session, workspace_id, current_user.id, ai_engine, feishu
    ).adopt_reply(reply_id, payload.model_dump(mode="json"))
    return success_response(request, ExpertReplyRead.model_validate(reply).model_dump(mode="json"))
