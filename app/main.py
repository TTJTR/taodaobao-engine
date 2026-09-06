from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.database import close_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.file_storage_root.mkdir(parents=True, exist_ok=True)
    settings.tender_storage_root.mkdir(parents=True, exist_ok=True)
    settings.presentation_export_dir.mkdir(parents=True, exist_ok=True)
    settings.integration_state_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield
    finally:
        await close_database()


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(
        title="淘到宝引擎 API",
        version="0.5.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    if settings.parsed_cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.parsed_cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Accept", "Content-Type", "Idempotency-Key", "X-File-Name"],
        )
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.api_prefix)
    application.mount("/", StaticFiles(directory="static", html=True), name="static")
    return application


app = create_app()
