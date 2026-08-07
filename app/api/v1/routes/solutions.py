import uuid

from fastapi import APIRouter, Request

from app.api.deps import DatabaseSession, WorkspaceId
from app.core.errors import AppError, ErrorCode
from app.core.responses import success_response
from app.db.repositories import SolutionRunRepository
from app.schemas.chat import SolutionRunRead

router = APIRouter()


@router.get("/{run_id}")
async def get_solution_run(
    run_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    run = await SolutionRunRepository(session, workspace_id).get(run_id)
    if run is None:
        raise AppError(ErrorCode.VALIDATION_FAILED, "方案运行不存在", status_code=404)
    return success_response(
        request, SolutionRunRead.model_validate(run).model_dump(mode="json")
    )
