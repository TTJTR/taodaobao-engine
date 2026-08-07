from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Database pools and external adapters will be initialized here.
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="淘到宝引擎 API",
        version="0.5.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.api_prefix)
    application.mount("/", StaticFiles(directory="static", html=True), name="static")
    return application


app = create_app()
