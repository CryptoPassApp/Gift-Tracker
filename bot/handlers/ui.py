"""UI-хелперы инлайн-экранов: меню — это одно редактируемое сообщение.

Все callback-кнопки показывают новый экран через ``edit_text`` текущего
сообщения. Новые сообщения шлют только команды (/start, /alerts) и
fallback, когда старое сообщение редактировать нельзя.
"""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup


async def edit_screen(
    callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup
) -> None:
    """Показывает экран, редактируя сообщение с нажатой кнопкой.

    Не отвечает на callback — вызывающий хендлер сам вызывает
    ``callback.answer(...)`` (иногда нужен тост с текстом). «message is
    not modified» (повторный клик по тому же экрану) — не ошибка, молча
    пропускаем. Битое/чужое сообщение — шлём экран новым сообщением.
    """
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            pass
        else:
            await callback.message.answer(text, reply_markup=kb)


__all__ = ["edit_screen"]
