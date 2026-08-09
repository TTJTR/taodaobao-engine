import uuid

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentUser, DatabaseSession, WorkspaceId
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.repositories import SolutionRunRepository
from app.schemas.chat import SolutionRunRead
from app.schemas.presentations import CreatePresentationRequest
from app.schemas.trust import TrustReviewRequest
from app.services.presentation_service import PresentationService
from app.services.solution_trust_service import SolutionTrustService

router = APIRouter(route_class=IdempotencyRoute)


@router.get("/{run_id}")
async def get_solution_run(
    run_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
) -> dict[str, object]:
    run = await SolutionRunRepository(session, workspace_id).get(run_id)
    if run is None:
        raise AppError(ErrorCode.VALIDATION_FAILED, "方案运行不存在", status_code=404)
    data = SolutionRunRead.model_validate(run).model_dump(mode="json")
    decision, claims_summary = await SolutionTrustService(
        session, workspace_id, current_user.id
    ).summary(run_id)
    data["trust_decision"] = decision
    data["claims_summary"] = claims_summary
    return success_response(request, data)


@router.get("/{run_id}/trust-report")
async def get_trust_report(
    run_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
) -> dict[str, object]:
    report = await SolutionTrustService(session, workspace_id, current_user.id).trust_report(run_id)
    return success_response(request, report)


@router.post("/{run_id}/trust-review")
async def review_solution_trust(
    run_id: uuid.UUID,
    payload: TrustReviewRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    decision = await SolutionTrustService(session, workspace_id, current_user.id).review(
        run_id, payload
    )
    return success_response(request, decision)


@router.post("/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_solution_run(
    run_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    run = await SolutionTrustService(session, workspace_id, current_user.id).retry(run_id)
    return success_response(
        request,
        {"run_id": str(run.id), "status": run.status.value, "stage": run.stage},
    )


@router.post("/{run_id}/presentations", status_code=status.HTTP_202_ACCEPTED)
async def create_presentation(
    run_id: uuid.UUID,
    payload: CreatePresentationRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    item = await PresentationService(session, workspace_id, current_user.id).create_presentation(
        run_id, payload
    )
    return success_response(
        request,
        {
            "presentation_id": str(item.id),
            "status": item.status.value,
            "trace_id": item.trace_id,
            "poll_after_ms": 1000,
        },
    )
