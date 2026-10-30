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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import alerts, demo, gifts, internal, user
from bot.config import get_settings
from bot.database import Database
from bot.logging_setup import configure_logging
from bot.services.heartbeat import heartbeat_status
from bot.services.maintenance import MaintenanceService
from bot.services.market_api import MarketAPI

logger = logging.getLogger("gifttracker.api")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def create_app(settings: "Settings | None" = None) -> FastAPI:
    """Собирает приложение: CORS -> роутеры -> статика (монтируется последней).

    ``settings`` можно передать в тестах; по умолчанию берется get_settings().
    """
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Инициализация БД и общих сервисов на процесс API."""
        db = Database(settings)
        await db.init()
        maintenance = MaintenanceService(db, settings)
        await maintenance.start()

        app.state.settings = settings
        app.state.db = db
        app.state.market = MarketAPI(settings)
        app.state.maintenance = maintenance

        logger.info(
            "API ready (market_source=%s, static=%s)", settings.MARKET_SOURCE, STATIC_DIR
        )
        try:
            yield
        finally:
            await maintenance.stop()
            try:
                await db.checkpoint_wal()
            except Exception:  # noqa: BLE001 — финальный checkpoint не критичен
                logger.warning("Final WAL checkpoint failed", exc_info=True)
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

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        """Security hardening: защита от утечек initData через Referer, MIME sniffing и clickjacking."""
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org https://*.t.me;"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.get("/api/health", tags=["health"])
    async def health(request: Request) -> dict[str, object]:
        """Healthcheck для Docker/мониторинга.

        ``status`` отражает только API (иначе Docker пометил бы api
        нездоровым при перезапуске бота); состояние бота — в ``bot``,
        пауза сканера после лимита TonAPI — в ``market``.
        """
        market = getattr(request.app.state, "market", None)
        cooldown = round(market.tonapi_cooldown_left) if market is not None else 0
        return {
            "status": "ok",
            "app": "gifttracker",
            "version": "1.0.0",
            "bot": heartbeat_status(settings),
            "market": {"tonapi_cooldown_sec": cooldown},
        }

    # Chrome Private Network Access: страница публичного сайта (web.telegram.org)
    # при запросе к localhost должна получить этот заголовок на preflight,
    # иначе браузер молча блокирует запрос (нужно для MRKT-хука).
    @app.middleware("http")
    async def _private_network_header(request: Request, call_next):
        response = await call_next(request)
        if request.method == "OPTIONS":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    app.include_router(gifts.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    app.include_router(user.router, prefix="/api")
    app.include_router(demo.router, prefix="/api")
    # Внутренний хук initData MRKT (защищен секретом из data/_mrkt_hook_secret).
    app.include_router(internal.router, prefix="/api")

    # Статика Mini App монтируется ПОСЛЕДНЕЙ, чтобы не перекрывать /api/*.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()

__all__ = ["app", "create_app"]
