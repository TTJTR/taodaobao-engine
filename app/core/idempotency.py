import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Header, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select, text

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import InvalidSessionError, SessionCodec
from app.db.database import get_session_factory
from app.db.models import IdempotencyRecord

IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENT_METHODS = {"POST", "PATCH"}
ResponseHandler = Callable[[], Awaitable[Response]]


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


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


async def build_request_hash(request: Request) -> str:
    body = await request.body()
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0]
    if body and content_type == "application/json":
        try:
            body = _canonical_json(json.loads(body))
        except json.JSONDecodeError:
            pass

    query = sorted(request.query_params.multi_items())
    digest = hashlib.sha256()
    for part in (
        request.method.upper().encode("ascii"),
        request.url.path.encode("utf-8"),
        _canonical_json(query),
        body,
    ):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def get_request_workspace_id(request: Request) -> uuid.UUID | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    try:
        codec = SessionCodec(settings.session_secret, settings.session_ttl_seconds)
        return codec.decode(token).workspace_id
    except InvalidSessionError:
        return None


def _advisory_lock_id(workspace_id: uuid.UUID, key: str) -> int:
    digest = hashlib.sha256(f"{workspace_id}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _serialize_response(response: Response) -> dict[str, Any] | None:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        return None
    media_type = (response.media_type or "").split(";", maxsplit=1)[0]
    if media_type != "application/json":
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return {
        "body": payload,
        "status_code": response.status_code,
    }


def _restore_response(response_data: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        content=response_data["body"],
        status_code=int(response_data["status_code"]),
        headers={"X-Idempotent-Replay": "true"},
    )


class PostgresIdempotencyBackend:
    async def execute(
        self,
        *,
        workspace_id: uuid.UUID,
        key: str,
        request_hash: str,
        handler: ResponseHandler,
    ) -> Response:
        now = datetime.now(UTC)
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _advisory_lock_id(workspace_id, key)},
            )
            record = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.workspace_id == workspace_id,
                    IdempotencyRecord.key == key,
                    IdempotencyRecord.expires_at > now,
                )
            )
            if record is not None:
                if record.request_hash != request_hash:
                    raise AppError(
                        ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                        "Idempotency-Key 已用于不同请求",
                        status_code=409,
                    )
                return _restore_response(record.response_data)

            await session.execute(
                IdempotencyRecord.__table__.delete().where(
                    IdempotencyRecord.workspace_id == workspace_id,
                    IdempotencyRecord.key == key,
                    IdempotencyRecord.expires_at <= now,
                )
            )
            response = await handler()
            if 200 <= response.status_code < 300:
                response_data = _serialize_response(response)
                if response_data is not None:
                    session.add(
                        IdempotencyRecord(
                            key=key,
                            workspace_id=workspace_id,
                            request_hash=request_hash,
                            response_data=response_data,
                            expires_at=now + IDEMPOTENCY_TTL,
                        )
                    )
            return response


def get_idempotency_backend() -> PostgresIdempotencyBackend:
    return PostgresIdempotencyBackend()


class IdempotencyRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        route_handler = super().get_route_handler()

        async def idempotent_route_handler(request: Request) -> Response:
            key = request.headers.get("Idempotency-Key")
            if request.method not in IDEMPOTENT_METHODS or key is None:
                return await route_handler(request)

            if not 16 <= len(key) <= 128:
                return await route_handler(request)

            workspace_id = get_request_workspace_id(request)
            if workspace_id is None:
                return await route_handler(request)

            request_hash = await build_request_hash(request)
            return await get_idempotency_backend().execute(
                workspace_id=workspace_id,
                key=key,
                request_hash=request_hash,
                handler=lambda: route_handler(request),
            )

        return idempotent_route_handler
