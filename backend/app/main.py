from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.logging import configure_logging
from app.db.pool import close_pool
from app.routes import chat, health, portfolios, resolve


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.backend_log_level)

    app = FastAPI(title="Stylobate Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(resolve.router)
    app.include_router(portfolios.router)

    return app


app = create_app()
