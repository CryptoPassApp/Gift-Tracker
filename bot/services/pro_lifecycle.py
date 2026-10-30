"""Часовой жизненный цикл PRO: снятие истекших подписок.

Запускается ботом отдельным фоновым таском. Первый проход выполняется
сразу на старте (после рестарта права истекших снимаются без ожидания
часа), далее — раз в ``PRO_EXPIRY_CHECK_INTERVAL_SEC`` секунд.

Ленивая проверка в ``get_or_create_user`` и ``User.is_pro_active()``
остается страховкой; джоба нужна, чтобы истекший PRO терял мгновенные
пуши и безлимит даже если пользователь не заходит в бота.
"""
from __future__ import annotations

import asyncio
import logging

from bot.config import Settings
from bot.database import Database

logger = logging.getLogger(__name__)


class ProExpiryService:
    """Периодическое понижение истекших PRO-подписок."""

    def __init__(self, db: Database, settings: Settings, *, interval: float | None = None) -> None:
        self.db = db
        self.settings = settings
        self.interval = interval if interval is not None else settings.PRO_EXPIRY_CHECK_INTERVAL_SEC
        self._task: asyncio.Task | None = None

    async def run_once(self) -> int:
        """Один проход; возвращает число снятых PRO-статусов."""
        return await self.db.expire_pro_subscriptions()

    async def run(self) -> None:
        logger.info("PRO expiry job started (interval=%ss)", self.interval)
        while True:
            try:
                await self.run_once()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                logger.info("PRO expiry job cancelled")
                raise
            except Exception:  # noqa: BLE001 — джоба не должна ронять бота
                logger.exception("PRO expiry job failed")
                await asyncio.sleep(self.interval)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="pro-expiry")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


__all__ = ["ProExpiryService"]
