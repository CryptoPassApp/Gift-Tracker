"""Валидация Telegram WebApp initData (HMAC-SHA256) и извлечение текущего пользователя.

Алгоритм по спецификации Telegram:
  1. parse_qs(initData), извлекаем и удаляем ``hash``;
  2. data_check_string = отсортированные пары "key=value", соединенные "\\n";
  3. secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token);
  4. expected  = HMAC_SHA256(key=secret_key, msg=data_check_string);
  5. expected == hash (сравнение в константное время).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import Header, HTTPException, Request, status

from bot.models import User

logger = logging.getLogger(__name__)

INSECURE_DEFAULT_TOKENS = {
    "",
    "123456789:PLACEHOLDER-TOKEN-REPLACE-ME",
    "dummy_token",
    "CHANGE_ME",
}


def validate_init_data(
    init_data: str, bot_token: str, max_age_sec: int | None = None
) -> dict[str, Any]:
    """Валидирует подпись initData и возвращает разобранный payload пользователя.

    Raises:
        HTTPException 401 при несовпадении подписи, битом формате или истекшем сроке.
        HTTPException 500 при небезопасном токене-заглушке.
    """
    if bot_token in INSECURE_DEFAULT_TOKENS:
        logger.critical("Security check failed: attempting initData validation with default/insecure BOT_TOKEN")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: BOT_TOKEN is not configured securely",
        )

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

        # Защита от Replay Attack: проверка срока давности auth_date
        if max_age_sec is not None and max_age_sec > 0:
            if "auth_date" not in params:
                raise ValueError("missing auth_date in initData")
            try:
                auth_date = int(params["auth_date"][0])
            except (ValueError, TypeError):
                raise ValueError("invalid auth_date")
            now = time.time()
            if now - auth_date > max_age_sec:
                raise ValueError("initData expired (auth_date too old)")
            if auth_date - now > 120:
                raise ValueError("initData timestamp is in future")

        user_payload = json.loads(params["user"][0])
        if not isinstance(user_payload, dict) or "id" not in user_payload:
            raise ValueError("user payload missing")
        return user_payload
    except HTTPException:
        raise
    except (KeyError, ValueError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("initData validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid initData signature",
        ) from None


async def get_current_user(
    request: Request,
    x_init_data: str | None = Header(default=None, description="Telegram WebApp initData"),
) -> User:
    """FastAPI-зависимость: валидирует initData и достает User из БД."""
    if not x_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Init-Data header",
        )
    settings = request.app.state.settings
    max_age = getattr(settings, "INIT_DATA_MAX_AGE_SEC", 86400)
    payload = validate_init_data(x_init_data, settings.BOT_TOKEN, max_age_sec=max_age)

    db = request.app.state.db
    from bot.database import get_or_create_user

    async with db.session() as session:
        user = await get_or_create_user(session, payload)
        return user


__all__ = ["validate_init_data", "get_current_user"]
