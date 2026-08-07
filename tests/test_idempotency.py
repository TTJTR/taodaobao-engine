import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core import idempotency
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.idempotency import IdempotencyRoute
from app.core.security import SessionCodec


class MutationBody(BaseModel):
    name: str


class InMemoryIdempotencyBackend:
    def __init__(self) -> None:
        self.cache: dict[tuple[uuid.UUID, str], tuple[str, dict[str, Any]]] = {}

    async def execute(
        self,
        *,
        workspace_id: uuid.UUID,
        key: str,
        request_hash: str,
        handler: Callable[[], Awaitable[JSONResponse]],
    ) -> JSONResponse:
        cache_key = (workspace_id, key)
        cached = self.cache.get(cache_key)
        if cached is not None:
            cached_hash, response_data = cached
            if cached_hash != request_hash:
                from app.core.errors import AppError, ErrorCode

                raise AppError(
                    ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                    "Idempotency-Key 已用于不同请求",
                    status_code=409,
                )
            return idempotency._restore_response(response_data)

        response = await handler()
        response_data = idempotency._serialize_response(response)
        assert response_data is not None
        self.cache[cache_key] = (request_hash, response_data)
        return response


def create_idempotency_test_app(counter: dict[str, int]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter(route_class=IdempotencyRoute)

    @router.post("/mutations", status_code=201)
    async def create_mutation(body: MutationBody) -> dict[str, object]:
        counter["calls"] += 1
        return {"request_id": "req_test", "data": {"name": body.name}}

    app.include_router(router)
    return app


def test_same_idempotent_request_executes_business_logic_once(
    monkeypatch: Any,
) -> None:
    counter = {"calls": 0}
    backend = InMemoryIdempotencyBackend()
    monkeypatch.setattr(idempotency, "get_idempotency_backend", lambda: backend)
    client = TestClient(create_idempotency_test_app(counter))
    codec = SessionCodec(settings.session_secret, settings.session_ttl_seconds)
    client.cookies.set(
        settings.session_cookie_name,
        codec.encode(uuid.uuid4(), settings.demo_workspace_id),
    )
    headers = {"Idempotency-Key": "same-request-key-0001"}

    first = client.post("/mutations", json={"name": "demo"}, headers=headers)
    second = client.post("/mutations", json={"name": "demo"}, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["X-Idempotent-Replay"] == "true"
    assert counter["calls"] == 1


def test_same_key_with_different_body_returns_conflict(monkeypatch: Any) -> None:
    counter = {"calls": 0}
    backend = InMemoryIdempotencyBackend()
    monkeypatch.setattr(idempotency, "get_idempotency_backend", lambda: backend)
    client = TestClient(create_idempotency_test_app(counter))
    codec = SessionCodec(settings.session_secret, settings.session_ttl_seconds)
    client.cookies.set(
        settings.session_cookie_name,
        codec.encode(uuid.uuid4(), settings.demo_workspace_id),
    )
    headers = {"Idempotency-Key": "conflicting-key-0001"}

    first = client.post("/mutations", json={"name": "first"}, headers=headers)
    conflict = client.post("/mutations", json={"name": "second"}, headers=headers)

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert counter["calls"] == 1
