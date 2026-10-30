"""Docker healthcheck бота: ``python -m bot.healthcheck``.

Exit code 0 — бот пишет свежий heartbeat; 1 — файл отсутствует, устарел
или содержит статус ``stopped``. Используется в docker-compose.yml.
"""
from __future__ import annotations

import json

from bot.config import Settings, get_settings
from bot.services.heartbeat import heartbeat_status


def main(settings: Settings | None = None) -> int:
    status = heartbeat_status(settings or get_settings())
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["alive"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["main"]
