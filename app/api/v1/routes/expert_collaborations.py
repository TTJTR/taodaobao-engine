import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from app.api.deps import (
    AIEngineDependency,
    CurrentUser,
    DatabaseSession,
    FeishuAdapterDependency,
    WorkspaceId,
    get_embedding_provider,
    get_feishu_adapter,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.database import get_session_factory
from app.integrations.feishu_events import (
    FeishuEventError,
    decode_event_body,
    parse_text_message_event,
    verify_event_signature,
)
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
    resolve_collaboration_by_feishu_group,
    resolve_collaboration_workspace,
)
from app.services.model_connection_service import workspace_ai_engine
from app.services.research_pipeline import run_research_pipeline

router = APIRouter(route_class=IdempotencyRoute)
events_router = APIRouter(route_class=IdempotencyRoute)


def serialize_collaboration(item) -> dict:
    return ExpertCollaborationRead.model_validate(item).model_dump(mode="json")


def _verify_feishu_token(payload: dict) -> None:
    if settings.feishu_mode != "live":
        return
    configured = settings.feishu_verification_token or ""
    supplied = str(payload.get("token") or (payload.get("header") or {}).get("token") or "")
    if not configured or not hmac.compare_digest(configured, supplied):
        raise AppError(ErrorCode.AUTH_REQUIRED, "飞书事件校验失败", status_code=401)


def _question_id_for_message(collaboration, text: str) -> str:
    questions = collaboration.questions or []
    explicit = [
        str(item["question_id"]) for item in questions if str(item.get("question_id") or "") in text
    ]
    if explicit:
        return explicit[0]
    if len(questions) == 1:
        return str(questions[0]["question_id"])
    raise AppError(
        ErrorCode.VALIDATION_FAILED,
        "群内回复需包含问题编号，例如 EXP-Q1",
        status_code=422,
    )


@events_router.post("/events/feishu", status_code=status.HTTP_200_OK)
async def receive_native_feishu_event(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    raw_body = await request.body()
    try:
        verify_event_signature(
            raw_body,
            timestamp=request.headers.get("X-Lark-Request-Timestamp"),
            nonce=request.headers.get("X-Lark-Request-Nonce"),
            signature=request.headers.get("X-Lark-Signature"),
            encrypt_key=settings.feishu_encrypt_key,
        )
        payload = decode_event_body(raw_body, settings.feishu_encrypt_key)
    except FeishuEventError as exc:
        raise AppError(ErrorCode.AUTH_REQUIRED, str(exc), status_code=401) from exc
    _verify_feishu_token(payload)
    if payload.get("type") == "url_verification":
        return {"challenge": str(payload.get("challenge") or "")}
    event = parse_text_message_event(payload)
    if event is None:
        return {"code": 0}
    embedding_provider = get_embedding_provider()
    feishu = get_feishu_adapter()
    async with get_session_factory()() as session:
        collaboration, workspace_id, user_id = await resolve_collaboration_by_feishu_group(
            session, event["chat_id"]
        )
        ai_engine = await workspace_ai_engine(session, workspace_id)
        candidate = next(
            (
                item
                for item in collaboration.candidate_records
                if item.get("feishu_user_id") == event["author_id"]
            ),
            None,
        )
        author_name = str((candidate or {}).get("display_name") or event["author_id"])
        reply, task = await ExpertCollaborationService(
            session, workspace_id, user_id, ai_engine, feishu
        ).record_reply(
            collaboration_id=collaboration.id,
            question_id=_question_id_for_message(collaboration, event["text"]),
            author_id=event["author_id"],
            author_name=author_name,
            answer_text=event["text"],
            feishu_message_id=event["message_id"],
            message_url=f"feishu-message://{event['message_id']}",
        )
        reply_id = reply.id
        task_id = task.id
    background_tasks.add_task(
        run_research_pipeline,
        task_id,
        workspace_id,
        user_id,
        ai_engine,
        embedding_provider,
    )
    return {"code": 0, "data": {"reply_id": str(reply_id)}}


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
    feishu: FeishuAdapterDependency,
) -> dict[str, object]:
    if settings.feishu_mode == "live":
        configured = settings.feishu_verification_token or ""
        supplied = payload.verification_token or ""
        if not configured or not hmac.compare_digest(configured, supplied):
            raise AppError(ErrorCode.AUTH_REQUIRED, "飞书事件校验失败", status_code=401)
    workspace_id, user_id = await resolve_collaboration_workspace(session, payload.collaboration_id)
    ai_engine = await workspace_ai_engine(session, workspace_id)
    embedding_provider = get_embedding_provider()
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
