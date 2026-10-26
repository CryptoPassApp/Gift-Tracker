"""Валидация Telegram WebApp initData (HMAC-SHA256) и зависимость текущего пользователя.

Алгоритм из документации Telegram:
  1. parse_qs(initData), достать и убрать ``hash``;
  2. data_check_string = отсортированные пары "key=value", склеенные "\\n";
  3. secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token);
  4. expected  = HMAC_SHA256(key=secret_key, msg=data_check_string);
  5. expected == hash (безопасное сравнение).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib.parse import parse_qs

from fastapi import Header, HTTPException, Request, status

from bot.models import User


def validate_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    """Проверяет подпись initData и возвращает распарсенный payload пользователя.

    Raises:
        HTTPException 401 — если подпись неверна или данные повреждены.
    """
    try:
        params = parse_qs(init_data, keep_blank_values=True)
        received_hash = params["hash"][0]

        data_check_string = "\n".join(
            f"{key}={params[key][0]}" for key in sorted(k for k in params if k != "hash")
        )

        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("hash mismatch")

        user_payload = json.loads(params["user"][0])
        if not isinstance(user_payload, dict) or "id" not in user_payload:
            raise ValueError("user payload missing")
        return user_payload
    except HTTPException:
        raise
    except (KeyError, ValueError, json.JSONDecodeError, IndexError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid initData signature: {exc}",
        ) from exc


async def get_current_user(
    request: Request,
    x_init_data: str | None = Header(default=None, description="Telegram WebApp initData"),
) -> User:
    """FastAPI-зависимость: валидирует initData и возвращает User из БД.

    Без заголовка или с битой подписью — 401 (не 422: заголовок сделан
    опциональным на уровне схемы, чтобы FastAPI не отбивал запрос
    ошибкой валидации раньше авторизации).
    """
    if not x_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Init-Data header",
        )
    settings = request.app.state.settings
    payload = validate_init_data(x_init_data, settings.BOT_TOKEN)

    db = request.app.state.db
    from bot.database import get_or_create_user  # локальный импорт — избегаем цикла

    async with db.session() as session:
        user = await get_or_create_user(session, payload)
        return user


__all__ = ["validate_init_data", "get_current_user"]
