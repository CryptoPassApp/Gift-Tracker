"""Настройка логирования GiftTracker: уровень из .env и JSON-формат.

``LOG_FORMAT=json`` включает структурные логи (по одной JSON-строке на
запись) — удобно для сборщиков вроде Loki/Filebeat. ``LOG_LEVEL``
принимает стандартные имена (DEBUG/INFO/WARNING/ERROR/CRITICAL);
пустое или неизвестное значение безопасно трактуется как INFO.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

from bot.config import Settings, get_settings

# Атрибуты, которые есть у каждого LogRecord: их не дублируем в JSON.
_RESERVED_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=None, exc_info=None))
)


def resolve_level(value: Any) -> int:
    """Строковый/числовой уровень -> logging-константа (fallback INFO)."""
    if isinstance(value, bool) or value is None:
        return logging.INFO
    if isinstance(value, int):
        return value if value > 0 else logging.INFO
    candidate = logging.getLevelName(str(value).strip().upper())
    if isinstance(candidate, int) and candidate > 0:
        return candidate
    return logging.INFO


class JsonFormatter(logging.Formatter):
    """Форматтер одной JSON-строки на запись лога."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Поля из logger.info(..., extra={...}) — например, user_id или lot_url.
        for key, value in vars(record).items():
            if key not in _RESERVED_RECORD_ATTRS:
                payload.setdefault(key, value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    settings: Settings | None = None,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Настраивает корневой логгер (идемпотентно).

    Убирает только собственный handler предыдущего вызова, не трогая
    сторонние (например, caplog в pytest). Uvicorn-логгеры переводятся
    на корневой handler, чтобы формат был единым.
    """
    settings = settings or get_settings()
    level = resolve_level(settings.LOG_LEVEL)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_gifttracker_handler", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stdout)
    setattr(handler, "_gifttracker_handler", True)
    handler.setLevel(level)
    if (settings.LOG_FORMAT or "").strip().lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
    root.addHandler(handler)

    # Uvicorn по умолчанию держит собственные handlers (свой формат).
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    return root


__all__ = ["JsonFormatter", "configure_logging", "resolve_level"]
