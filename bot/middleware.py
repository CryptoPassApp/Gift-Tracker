"""AuthMiddleware — авторегистрация пользователей и учет PRO-статуса.

Срабатывает на каждое Update, находит эффективного пользователя,
создает/обновляет запись в БД (внутри одной сессии с хендлером) и кладет
в workflow-data:
    ``db_user`` — ORM-объект User;
    ``session`` — открытая AsyncSession (commit выполняется на выходе).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Update, User as TgUser

from bot.database import Database, get_or_create_user

logger = logging.getLogger(__name__)

# Поля Update, в которых может жить from_user.
_UPDATE_USER_FIELDS = (
    "message",
    "edited_message",
    "business_message",
    "callback_query",
    "pre_checkout_query",
    "my_chat_member",
    "chat_member",
)


class AuthMiddleware(BaseMiddleware):
    """Авторегистрация + проброс сессии БД в хендлеры."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        tg_user = self._extract_user(event)
        if tg_user is None or tg_user.is_bot:
            # Служебные апдейты без пользователя — пропускаем без БД.
            return await handler(event, data)

        async with self.db.session() as session:
            db_user = await get_or_create_user(
                session,
                {"id": tg_user.id, "username": tg_user.username, "first_name": tg_user.first_name},
            )
            data["db_user"] = db_user
            data["session"] = session
            return await handler(event, data)

    @staticmethod
    def _extract_user(update: Update) -> TgUser | None:
        """Достает пользователя из любого типа апдейта."""
        for field_name in _UPDATE_USER_FIELDS:
            sub_event = getattr(update, field_name, None)
            if sub_event is None:
                continue
            user = getattr(sub_event, "from_user", None) or getattr(sub_event, "from", None)
            if isinstance(user, TgUser):
                return user
        return None


__all__ = ["AuthMiddleware"]
