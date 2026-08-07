import uuid

from fastapi import APIRouter, Request

from app.api.deps import DatabaseSession, WorkspaceId
from app.core.errors import AppError, ErrorCode
from app.core.responses import success_response
from app.db.repositories import JobRepository
from app.schemas.sources import JobRead

router = APIRouter()


@router.get("/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    job = await JobRepository(session, workspace_id).get(job_id)
    if job is None:
        raise AppError(ErrorCode.JOB_FAILED, "任务不存在", status_code=404)
    return success_response(request, JobRead.model_validate(job).model_dump(mode="json"))
