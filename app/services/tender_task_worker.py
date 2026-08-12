import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.db.database import get_session_factory
from app.db.models import RawArtifact, WorkflowTask, WorkflowTaskStatus
from app.services.tender_parse_worker import TenderParseWorker


def resolve_tender_source(storage_uri: str, storage_root: Path | None = None) -> Path:
    root = (storage_root or settings.tender_storage_root).resolve()
    parsed = urlsplit(storage_uri)
    if parsed.scheme not in {"", "file"} or parsed.query or parsed.fragment:
        raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "不受信任的招标制品地址", status_code=422)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "不允许远程文件地址", status_code=422)
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        candidate = Path(raw_path)
    else:
        candidate = Path(storage_uri)
    if not candidate.is_absolute():
        raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "招标制品地址必须是绝对路径", status_code=422)
    source = candidate.resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "招标制品不在受信任存储目录", status_code=422)
    return source


async def run_tender_parse_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    tender_id: uuid.UUID,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.workspace_id == workspace_id,
                WorkflowTask.kind == "tender_parse",
                WorkflowTask.target_id == tender_id,
                WorkflowTask.is_deleted.is_(False),
            ).with_for_update()
        )
        if task is None:
            return
        artifact_id = _artifact_id(task.payload)
        artifact = await session.scalar(
            select(RawArtifact).where(
                RawArtifact.id == artifact_id,
                RawArtifact.workspace_id == workspace_id,
                RawArtifact.is_deleted.is_(False),
            )
        )
        try:
            if artifact is None or not artifact.storage_uri or not artifact.mime_type:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    "解析任务引用的原始制品不可用",
                    status_code=404,
                )
            task.stage = "validating_file"
            await session.commit()
            source = resolve_tender_source(artifact.storage_uri)
            if source.stat().st_size != artifact.byte_size:
                raise AppError(
                    ErrorCode.TENDER_FILE_UNSAFE, "招标制品大小校验失败", status_code=422
                )
            if _sha256_file(source) != artifact.content_sha256:
                raise AppError(
                    ErrorCode.TENDER_FILE_UNSAFE, "招标制品指纹校验失败", status_code=422
                )
            task.stage = "parsing_document"
            await session.commit()
            parsed = await TenderParseWorker(session, workspace_id).parse(
                tender_id=tender_id,
                raw_artifact_id=artifact.id,
                source=source,
                mime_type=artifact.mime_type,
                filename=artifact.source_filename,
            )
            task.status = WorkflowTaskStatus.COMPLETED
            task.stage = "completed"
            task.payload = {
                **task.payload,
                "parse_version_id": str(parsed.id),
                "requirement_count": parsed.resource_usage.get("requirement_count", 0),
            }
            task.finished_at = datetime.now(UTC)
            task.error_code = None
            task.error_summary = None
        except Exception as exc:
            await session.rollback()
            task = await session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.id == task_id,
                    WorkflowTask.workspace_id == workspace_id,
                    WorkflowTask.is_deleted.is_(False),
                ).with_for_update()
            )
            if task is None:
                return
            task.status = WorkflowTaskStatus.FAILED
            task.stage = "failed"
            if isinstance(exc, AppError):
                task.error_code = exc.code.value
                task.error_summary = exc.message[:1000]
            else:
                task.error_code = ErrorCode.TENDER_PARSE_FAILED.value
                task.error_summary = str(exc)[:1000]
            task.finished_at = datetime.now(UTC)
        task.lease_owner = None
        task.lease_expires_at = None
        await session.commit()


def _artifact_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload["raw_artifact_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(ErrorCode.VALIDATION_FAILED, "解析任务载荷无效", status_code=422) from exc


def _sha256_file(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
