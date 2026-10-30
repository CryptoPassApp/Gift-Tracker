"""Heartbeat бота: файл-маркер живости.

Бот периодически пишет ``BOT_HEARTBEAT_FILE`` (по умолчанию
``./data/bot_heartbeat.json``). Файл читают:

* ``GET /api/health`` — отдает состояние бота рядом с состоянием API;
* ``python -m bot.healthcheck`` — Docker healthcheck сервиса ``bot``
  (exit code 0 — жив, 1 — heartbeat отсутствует/устарел).

Запись атомарная (temp-файл + ``os.replace``), чтобы читатель никогда
не увидел наполовину записанный JSON.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import Settings

logger = logging.getLogger(__name__)

HEARTBEAT_VERSION = 1
DEFAULT_HEARTBEAT_FILE = "./data/bot_heartbeat.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def heartbeat_path(settings: Settings) -> Path:
    """Путь к файлу heartbeat (пустая настройка -> дефолт)."""
    raw = (settings.BOT_HEARTBEAT_FILE or "").strip() or DEFAULT_HEARTBEAT_FILE
    return Path(raw)


def write_heartbeat(
    settings: Settings,
    *,
    status: str = "ok",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Пишет heartbeat; ошибки записи логируются, но не роняют бота."""
    path = heartbeat_path(settings)
    payload: dict[str, Any] = {
        "version": HEARTBEAT_VERSION,
        "status": status,
        "pid": os.getpid(),
        "timestamp": _now_iso(),
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if value is not None})

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        logger.exception("Failed to write heartbeat file %s", path)
    return payload


def read_heartbeat(settings: Settings) -> dict[str, Any] | None:
    """Читает heartbeat; ``None`` если файла нет или он поврежден."""
    path = heartbeat_path(settings)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def heartbeat_status(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Сводка для /api/health: жив ли бот и когда он последний раз отчитывался."""
    data = read_heartbeat(settings)
    if data is None:
        return {
            "alive": False,
            "status": "missing",
            "age_sec": None,
            "last_seen": None,
            "pid": None,
            "scanner_last_scan": None,
        }

    stamp = _parse_ts(data.get("timestamp"))
    current = now or datetime.now(timezone.utc)
    age = max((current - stamp).total_seconds(), 0.0) if stamp is not None else None
    stale_after = float(settings.BOT_HEARTBEAT_STALE_SEC)
    alive = (
        age is not None
        and age <= stale_after
        and str(data.get("status")) == "ok"
    )
    return {
        "alive": alive,
        "status": data.get("status"),
        "age_sec": round(age, 1) if age is not None else None,
        "last_seen": data.get("timestamp"),
        "pid": data.get("pid"),
        "scanner_last_scan": data.get("scanner_last_scan"),
    }


class Heartbeat:
    """Периодический писатель heartbeat (фоновый таск бота)."""

    def __init__(self, settings: Settings, *, interval: float | None = None) -> None:
        self.settings = settings
        self.interval = interval if interval is not None else settings.BOT_HEARTBEAT_INTERVAL_SEC
        self._started_at = time.monotonic()
        self._last_scan: str | None = None
        self._task: asyncio.Task | None = None

    def mark_scan(self) -> None:
        """Отметить успешный проход SniperEngine (для диагностики)."""
        self._last_scan = _now_iso()

    def _extra(self) -> dict[str, Any]:
        return {
            "scanner_last_scan": self._last_scan,
            "uptime_sec": round(time.monotonic() - self._started_at, 1),
            # Нужен start_local.ps1: после смены туннеля бот со старым URL
            # остается «живым» по heartbeat, но кнопка ведет на мертвый домен.
            "mini_app_url": self.settings.MINI_APP_URL,
        }

    def beat(self, status: str = "ok") -> dict[str, Any]:
        return write_heartbeat(self.settings, status=status, extra=self._extra())

    async def run(self) -> None:
        """Пишет heartbeat сразу и далее раз в ``interval`` секунд."""
        self.beat("ok")
        while True:
            try:
                await asyncio.sleep(self.interval)
                self.beat("ok")
            except asyncio.CancelledError:
                raise

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="bot-heartbeat")

    async def stop(self) -> None:
        """Останавливает таск и оставляет финальный статус ``stopped``."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.beat("stopped")


__all__ = [
    "DEFAULT_HEARTBEAT_FILE",
    "HEARTBEAT_VERSION",
    "Heartbeat",
    "heartbeat_path",
    "heartbeat_status",
    "read_heartbeat",
    "write_heartbeat",
]
