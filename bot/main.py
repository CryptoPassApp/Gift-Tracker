"""Точка входа Telegram-бота GiftTracker (aiogram 3, long polling).

Запуск: ``python -m bot.main``

Что делает при старте:
  * настраивает логирование (уровень/JSON из .env);
  * инициализирует БД (создание таблиц, WAL);
  * регистрирует AuthMiddleware и все роутеры;
  * запускает heartbeat, обслуживание SQLite и фоновый SniperEngine;
  * выставляет меню команд бота.

При остановке: отменяет сканер и обслуживание, дожидается отправок
notifier, оставляет финальный heartbeat ``stopped`` и закрывает БД.
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
from bot.logging_setup import configure_logging
from bot.middleware import AuthMiddleware
from bot.services.crypto_payments import CryptoPaymentService
from bot.services.heartbeat import Heartbeat
from bot.services.maintenance import MaintenanceService
from bot.services.market_api import MarketAPI
from bot.services.notifier import Notifier
from bot.services.pro_lifecycle import ProExpiryService
from bot.services.sniper_engine import SniperEngine

logger = logging.getLogger("gifttracker.bot")


async def on_startup(bot: Bot, dispatcher: Dispatcher) -> None:
    """Регистрирует команды, запускает heartbeat, обслуживание и сканер."""
    settings = get_settings()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="⚡ Открыть терминал"),
            BotCommand(command="alerts", description="🔔 Мои алерты"),
            BotCommand(command="pro", description="👑 Купить PRO за Stars"),
            BotCommand(command="settings", description="🎚 PRO-фильтр дисконта"),
            BotCommand(command="top", description="🏆 Топ-5 снайпов радара"),
        ]
    )
    if settings.MINI_APP_URL and settings.MINI_APP_URL.startswith("https://"):
        try:
            from aiogram.types import MenuButtonWebApp, WebAppInfo
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Открыть",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL),
                )
            )
        except Exception:
            logger.warning("Failed to update chat menu button", exc_info=True)

    heartbeat: Heartbeat = dispatcher.workflow_data["heartbeat"]
    await heartbeat.start()

    maintenance: MaintenanceService = dispatcher.workflow_data["maintenance"]
    await maintenance.start()

    pro_expiry: ProExpiryService = dispatcher.workflow_data["pro_expiry"]
    await pro_expiry.start()

    crypto: CryptoPaymentService | None = dispatcher.workflow_data.get("crypto")
    if crypto is not None:
        await crypto.start()

    engine: SniperEngine = dispatcher.workflow_data["engine"]
    dispatcher.workflow_data["scanner_task"] = asyncio.create_task(
        engine.run(), name="sniper-engine"
    )
    logger.info("Bot startup complete (mini_app_url=%s)", settings.MINI_APP_URL)


async def on_shutdown(dispatcher: Dispatcher) -> None:
    """Аккуратно останавливает фоновые сервисы и дожидается отправок."""
    crypto: CryptoPaymentService | None = dispatcher.workflow_data.get("crypto")
    if crypto is not None:
        await crypto.stop()

    pro_expiry: ProExpiryService | None = dispatcher.workflow_data.get("pro_expiry")
    if pro_expiry is not None:
        await pro_expiry.stop()

    maintenance: MaintenanceService | None = dispatcher.workflow_data.get("maintenance")
    if maintenance is not None:
        await maintenance.stop()

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

    heartbeat: Heartbeat | None = dispatcher.workflow_data.get("heartbeat")
    if heartbeat is not None:
        await heartbeat.stop()

    db: Database | None = dispatcher.workflow_data.get("db")
    if db is not None:
        try:
            await db.checkpoint_wal()
        except Exception:  # noqa: BLE001 — финальный checkpoint не критичен
            logger.warning("Final WAL checkpoint failed", exc_info=True)
    logger.info("Bot stopped gracefully")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)

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
    heartbeat = Heartbeat(settings)
    engine = SniperEngine(
        market=market,
        db=db,
        notifier=notifier,
        settings=settings,
        on_scan=heartbeat.mark_scan,
    )
    maintenance = MaintenanceService(db, settings)
    pro_expiry = ProExpiryService(db, settings)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier.bind(bot)

    # Крипто-оплата (Crypto Pay): сервис только при заданном токене,
    # иначе кнопки скрыты и фоновая синхронизация не запускается.
    crypto_payments: CryptoPaymentService | None = None
    if settings.CRYPTO_PAY_TOKEN:
        crypto_payments = CryptoPaymentService(db, settings, bot=bot)
        logger.info("Crypto Pay enabled (asset=%s)", settings.CRYPTO_ASSET)

    dispatcher = Dispatcher()
    dispatcher.update.middleware(AuthMiddleware(db))
    dispatcher.workflow_data.update(
        {
            "db": db,
            "settings": settings,
            "market": market,
            "notifier": notifier,
            "engine": engine,
            "heartbeat": heartbeat,
            "maintenance": maintenance,
            "pro_expiry": pro_expiry,
            "crypto": crypto_payments,
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

    logger.info(
        "Starting long polling… (log_level=%s, log_format=%s)",
        settings.LOG_LEVEL,
        settings.LOG_FORMAT,
    )
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

__all__ = ["main", "on_shutdown", "on_startup"]
