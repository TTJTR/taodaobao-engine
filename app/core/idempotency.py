from typing import Annotated

from fastapi import Header

from app.core.errors import AppError, ErrorCode


async def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if idempotency_key is None or not 16 <= len(idempotency_key) <= 128:
        raise AppError(
            ErrorCode.VALIDATION_FAILED,
            "Idempotency-Key 必须为 16 至 128 个字符",
            status_code=422,
        )
    return idempotency_key


# The persistence layer will store workspace_id, user_id, key, request_hash,
# response payload and expires_at, with a unique constraint on the first three fields.

