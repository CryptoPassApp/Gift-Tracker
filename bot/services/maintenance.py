"""Фоновое обслуживание SQLite: WAL checkpoint и очистка seen_lots.

Сервис запускается и ботом, и API (каждый процесс — свой экземпляр).
Первый проход выполняется сразу на старте, затем — раз в
``WAL_CHECKPOINT_INTERVAL_SEC`` секунд. Ошибки не роняют процесс:
цикл логирует исключение и продолжает работу.
"""
from __future__ import annotations

import asyncio
import logging

from bot.config import Settings
from bot.database import Database

logger = logging.getLogger(__name__)


class MaintenanceService:
    """Периодический WAL checkpoint + ротация таблицы ``seen_lots``."""

    def __init__(self, db: Database, settings: Settings, *, interval: float | None = None) -> None:
        self.db = db
        self.settings = settings
        self.interval = interval if interval is not None else settings.WAL_CHECKPOINT_INTERVAL_SEC
        self._task: asyncio.Task | None = None

    async def run_once(self) -> tuple[dict[str, int] | None, int]:
        """Один проход обслуживания; возвращает (checkpoint, удалено отметок)."""
        checkpoint = await self.db.checkpoint_wal()
        pruned = await self.db.prune_seen_lots()
        snapshots = await self.db.prune_floor_snapshots(
            self.settings.FLOOR_SNAPSHOT_RETENTION_DAYS
        )
        await self.db.prune_snipe_signals(self.settings.SNIPE_SIGNALS_RETENTION_HOURS)

        if checkpoint is not None:
            if checkpoint.get("busy"):
                logger.debug("WAL checkpoint deferred (busy=1): %s", checkpoint)
            else:
                logger.debug("WAL checkpoint done: %s", checkpoint)
        if pruned:
            logger.info(
                "Pruned %d seen_lots row(s) older than %d day(s)",
                pruned,
                self.settings.SEEN_LOTS_RETENTION_DAYS,
            )
        if snapshots:
            logger.info(
                "Pruned %d floor snapshot(s) older than %d day(s)",
                snapshots,
                self.settings.FLOOR_SNAPSHOT_RETENTION_DAYS,
            )
        return checkpoint, pruned

    async def run(self) -> None:
        """Вечный цикл обслуживания (фоновый таск)."""
        logger.info(
            "Maintenance started (interval=%.0fs, seen_lots retention=%dd)",
            self.interval,
            self.settings.SEEN_LOTS_RETENTION_DAYS,
        )
        while True:
            try:
                await self.run_once()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                logger.info("Maintenance cancelled")
                raise
            except Exception:  # noqa: BLE001 — обслуживание не должно ронять процесс
                logger.exception("Maintenance cycle failed")
                await asyncio.sleep(self.interval)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="sqlite-maintenance")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


__all__ = ["MaintenanceService"]
