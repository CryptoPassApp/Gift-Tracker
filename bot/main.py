"""Точка входа Telegram-бота GiftTracker (aiogram 3, long polling).

Запуск: ``python -m bot.main``

Что делает при старте:
  * инициализирует БД (создание таблиц, WAL);
  * регистрирует AuthMiddleware и все роутеры;
  * запускает фоновый SniperEngine (сканер рынка + пуши);
  * выставляет меню команд бота.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import get_settings
from bot.database import Database
from bot.handlers import admin, alerts, start, subscription
from bot.middleware import AuthMiddleware
from bot.services.market_api import MarketAPI
from bot.services.notifier import Notifier
from bot.services.sniper_engine import SniperEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("gifttracker.bot")


async def on_startup(bot: Bot, dispatcher: Dispatcher) -> None:
    """Регистрирует команды и запускает фоновый сканер рынка."""
    settings = get_settings()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="⚡ Открыть Sniper Terminal"),
            BotCommand(command="alerts", description="🔔 Мои алерты"),
            BotCommand(command="pro", description="👑 Купить PRO за Stars"),
            BotCommand(command="top", description="🏆 Топ-5 снайпов радара"),
        ]
    )
    engine: SniperEngine = dispatcher.workflow_data["engine"]
    dispatcher.workflow_data["scanner_task"] = asyncio.create_task(engine.run())
    logger.info("Bot startup complete (mini_app_url=%s)", settings.MINI_APP_URL)


async def on_shutdown(dispatcher: Dispatcher) -> None:
    """Останавливает сканер и дожидается незавершенных отправок."""
    task: asyncio.Task | None = dispatcher.workflow_data.get("scanner_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    notifier: Notifier | None = dispatcher.workflow_data.get("notifier")
    if notifier is not None:
        await notifier.wait_pending()
    logger.info("Bot stopped gracefully")


async def main() -> None:
    settings = get_settings()

    if not settings.BOT_TOKEN or "PLACEHOLDER" in settings.BOT_TOKEN:
        logger.error(
            "BOT_TOKEN is not configured. Open gift_tracker/.env and set BOT_TOKEN "
            "to the token from @BotFather (/newbot)."
        )
        sys.exit(1)

    # Инфраструктура.
    db = Database(settings)
    await db.init()

    market = MarketAPI(settings)
    notifier = Notifier(settings)
    engine = SniperEngine(market=market, db=db, notifier=notifier, settings=settings)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier.bind(bot)

    dispatcher = Dispatcher()
    dispatcher.update.middleware(AuthMiddleware(db))
    dispatcher.workflow_data.update(
        {
            "db": db,
            "settings": settings,
            "market": market,
            "notifier": notifier,
            "engine": engine,
        }
    )

    dispatcher.include_routers(
        start.router,
        subscription.router,
        alerts.router,
        admin.router,
    )

    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    logger.info("Starting long polling…")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await db.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    # SystemExit (например, при отсутствии BOT_TOKEN) пробрасывается наружу,
    # чтобы процесс завершился с ненулевым кодом и это было видно в логах.


__all__ = ["main", "on_startup", "on_shutdown"]
