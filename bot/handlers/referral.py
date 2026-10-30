"""/ref — реферальная программа: ссылка, статистика, награды.

Награда: пригласивший получает +REFERRAL_REWARD_PCT% от подписки
приглашенного днями PRO (30 дн. * 15% = 5 дней за оплаченный месяц).
Начисление срабатывает на оплату Stars и крипту, идемпотентно
(уникальный payment_ref) — см. database.award_referral.

Экран редактирует текущее сообщение (как все экраны бота).
"""
from __future__ import annotations

import logging
from math import ceil

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import referral_stats
from bot.handlers.ui import edit_screen
from bot.keyboards import back_to_menu_kb, referral_kb
from bot.models import User

logger = logging.getLogger(__name__)

router = Router(name="referral")


def _referral_url(bot_username: str, telegram_id: int) -> str:
    """Deep-link ``/start ref_<tg_id>`` — в exactly том виде, что парсит start.py."""
    return f"https://t.me/{bot_username}?start=ref_{telegram_id}"


def _text(link: str, invited: int, earned_days: float, reward_days: float) -> str:
    return (
        "🤝 <b>Реферальная система</b>\n"
        "\n"
        "Приглашай друзей — получай дни PRO с их подписок.\n"
        f"Каждый оплаченный месяц друга = <b>+{reward_days:g} дней</b> тебе, "
        "начисляется автоматически в момент оплаты.\n"
        "\n"
        f"<b>Твоя ссылка:</b>\n<code>{link}</code>\n"
        "\n"
        f"👥 <b>Приглашено:</b> {invited}\n"
        f"🏆 <b>Заработано:</b> {earned_days:g} дн. PRO\n"
        "\n"
        "Дни суммируются с текущей подпиской — ничего подтверждать не нужно."
    )


async def _show_referral(
    target: Message | CallbackQuery, db_user: User, session: AsyncSession
) -> None:
    """Рисует экран рефки (message.answer либо edit_screen)."""
    settings = get_settings()
    bot: Bot = target.bot
    try:
        me = await bot.me()
        bot_username = me.username or "GiftTracker_App"
    except Exception:  # noqa: BLE001 — фолбэк на известный username
        bot_username = "GiftTracker_App"

    link = _referral_url(bot_username, db_user.telegram_id)
    invited, earned = await referral_stats(session, db_user)
    reward_days = float(ceil(settings.PRO_MONTH_DAYS * settings.REFERRAL_REWARD_PCT / 100))

    text = _text(link, invited, earned, reward_days)
    if isinstance(target, CallbackQuery):
        await edit_screen(target, text, referral_kb(link, settings.MINI_APP_URL))
    else:
        await target.answer(
            text, reply_markup=referral_kb(link, settings.MINI_APP_URL), disable_web_page_preview=True
        )


@router.message(Command("ref"))
async def cmd_referral(message: Message, session: AsyncSession, db_user: User) -> None:
    """/ref — экран рефералки."""
    await _show_referral(message, db_user, session)


@router.callback_query(F.data == "menu:ref")
async def cb_menu_referral(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """Кнопка «Рефералка» в меню — редактированием экрана."""
    await _show_referral(callback, db_user, session)
    await callback.answer()


@router.callback_query(F.data == "menu:ref:how")
async def cb_referral_how(callback: CallbackQuery) -> None:
    """Короткое «как это работает» внутри рефки."""
    settings = get_settings()
    text = (
        "🤝 <b>Как работает рефералка</b>\n"
        "\n"
        f"1️⃣ Делишься своей ссылкой (кнопка «Поделиться»).\n"
        "2️⃣ Друг жмет /start по ней — он закреплен за тобой навсегда.\n"
        f"3️⃣ Он оплачивает PRO (Stars или крипта) — тебе мгновенно начисляется "
        f"<b>+{settings.REFERRAL_REWARD_PCT}% от месяца</b> днями PRO.\n"
        "\n"
        "Начисление автоматическое и зашищено от накрутки: один платеж = одна "
        "награда, самореферал и повторные привязки не работают."
    )
    await edit_screen(callback, text, back_to_menu_kb())
    await callback.answer()


__all__ = ["router", "_referral_url"]
