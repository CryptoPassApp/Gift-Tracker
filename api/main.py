"""FastAPI-приложение GiftTracker: REST API + статика Mini App.

Запуск: ``uvicorn api.main:app --host 0.0.0.0 --port 8000``

Служит и витриной Mini App (static/index.html монтируется в корень),
и REST-бэкендом для Mini App внутри Telegram (валидация initData).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import alerts, demo, gifts, user
from bot.config import get_settings
from bot.database import Database
from bot.services.market_api import MarketAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("gifttracker.api")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def create_app(settings: "Settings | None" = None) -> FastAPI:
    """Собирает приложение: CORS -> роутеры -> статика (монтируется последней).

    ``settings`` можно передать в тестах; по умолчанию берется get_settings().
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Инициализация БД и общих сервисов на процесс API."""
        db = Database(settings)
        await db.init()

        app.state.settings = settings
        app.state.db = db
        app.state.market = MarketAPI(settings)

        logger.info(
            "API ready (market_source=%s, static=%s)", settings.MARKET_SOURCE, STATIC_DIR
        )
        try:
            yield
        finally:
            await db.dispose()
            logger.info("API stopped, database connections closed")

    app = FastAPI(
        title="GiftTracker API",
        description="Telegram Gifts Sniper, Floor Tracker & Web3 Arbitrage Terminal",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.API_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Healthcheck для Docker/мониторинга."""
        return {"status": "ok", "app": "gifttracker", "version": "1.0.0"}

    app.include_router(gifts.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    app.include_router(user.router, prefix="/api")
    app.include_router(demo.router, prefix="/api")

    # Статика Mini App монтируется ПОСЛЕДНЕЙ, чтобы не перекрывать /api/*.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()

__all__ = ["app", "create_app"]
