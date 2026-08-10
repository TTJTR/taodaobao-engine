import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import CurrentUser, DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.v2 import CreateRehearsalRequest, SubmitRehearsalTurnRequest
from app.services.rehearsal_service import RehearsalService

router = APIRouter(route_class=IdempotencyRoute)


def _session(row, *, include_context: bool = False) -> dict:
    data = {
        "id": str(row.id),
        "customer_profile_id": str(row.customer_profile_id),
        "solution_run_id": str(row.solution_run_id) if row.solution_run_id else None,
        "research_task_id": str(row.research_task_id) if row.research_task_id else None,
        "intelligence_snapshot_id": str(row.intelligence_snapshot_id)
        if row.intelligence_snapshot_id
        else None,
        "response_matrix_id": str(row.response_matrix_id) if row.response_matrix_id else None,
        "title": row.title,
        "role": row.role,
        "difficulty": row.difficulty,
        "focus_areas": row.focus_areas,
        "max_turns": row.max_turns,
        "status": row.status.value,
        "trace_id": row.trace_id,
        "current_turn": row.current_turn,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if include_context:
        data["context_snapshot"] = row.context_snapshot
    return data


def _turn(row) -> dict:
    return {
        "id": str(row.id),
        "sequence": row.sequence,
        "customer_question": row.customer_question,
        "employee_answer": row.employee_answer,
        "evaluation": row.evaluation,
        "created_at": row.created_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_rehearsal(
    payload: CreateRehearsalRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await RehearsalService(session, workspace_id, current_user.id).create(
        **payload.model_dump()
    )
    return success_response(request, _session(row, include_context=True))


@router.get("")
async def list_rehearsals(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    rows, total = await RehearsalService(session, workspace_id, current_user.id).list(
        page, page_size
    )
    return success_response(
        request,
        {
            "items": [_session(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{rehearsal_id}")
async def get_rehearsal(
    rehearsal_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    row, turns = await RehearsalService(session, workspace_id, current_user.id).detail(rehearsal_id)
    data = _session(row, include_context=True)
    data["turns"] = [_turn(turn) for turn in turns]
    return success_response(request, data)


@router.post("/{rehearsal_id}/start")
async def start_rehearsal(
    rehearsal_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    service = RehearsalService(session, workspace_id, current_user.id)
    row = await service.start(rehearsal_id)
    _, turns = await service.detail(rehearsal_id)
    data = _session(row)
    data["current_question"] = _turn(turns[-1])
    return success_response(request, data)


@router.post("/{rehearsal_id}/turns")
async def submit_rehearsal_turn(
    rehearsal_id: uuid.UUID,
    payload: SubmitRehearsalTurnRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    answered, next_turn = await RehearsalService(
        session, workspace_id, current_user.id
    ).submit_turn(rehearsal_id, payload.answer)
    return success_response(
        request,
        {
            "answered_turn": _turn(answered),
            "next_turn": _turn(next_turn) if next_turn else None,
            "can_complete": True,
        },
    )


@router.post("/{rehearsal_id}/complete")
async def complete_rehearsal(
    rehearsal_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await RehearsalService(session, workspace_id, current_user.id).complete(rehearsal_id)
    return success_response(
        request,
        {
            "id": str(row.id),
            "rehearsal_id": str(row.rehearsal_id),
            "score": row.score,
            "report_data": row.report_data,
            "schema_version": row.schema_version,
            "created_at": row.created_at.isoformat(),
        },
    )


@router.get("/{rehearsal_id}/report")
async def get_rehearsal_report(
    rehearsal_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    row = await RehearsalService(session, workspace_id, current_user.id).get_report(rehearsal_id)
    return success_response(
        request,
        {
            "id": str(row.id),
            "rehearsal_id": str(row.rehearsal_id),
            "score": row.score,
            "report_data": row.report_data,
            "schema_version": row.schema_version,
            "created_at": row.created_at.isoformat(),
        },
    )
