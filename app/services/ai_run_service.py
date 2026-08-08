import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIRunRecord, ProcessStatus


def persist_last_ai_run(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    engine,
    *,
    target_type: str,
    target_id: uuid.UUID,
    input_summary: dict[str, Any],
    request_id: str | None = None,
) -> AIRunRecord | None:
    metadata = getattr(engine, "last_run", None)
    if metadata is None:
        return None
    status_value = getattr(metadata.status, "value", str(metadata.status))
    record = AIRunRecord(
        workspace_id=workspace_id,
        request_id=request_id,
        trace_id=metadata.trace_id,
        target_type=target_type,
        target_id=target_id,
        method=metadata.method,
        stage=metadata.stage,
        status=(ProcessStatus.COMPLETED if status_value == "succeeded" else ProcessStatus.FAILED),
        model_version=metadata.model_version,
        prompt_version=metadata.prompt_version,
        schema_version=metadata.schema_version,
        embedding_version=metadata.embedding_version,
        duration_ms=round(metadata.duration_ms),
        error_code=metadata.error_code,
        input_summary=input_summary,
    )
    session.add(record)
    return record
