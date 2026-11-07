"""/ref — реферальная программа: ссылка, статистика, награды.

Ссылка строится из персонального случайного кода (database.ensure_referral_code),
а НЕ из telegram_id — шарить можно, не светя свой айди. Старые ссылки
``ref_<tg_id>`` продолжают работать (database.resolve_referral).

Награды (обе начисляются пригласившему автоматически):
1. За факт приглашения — +REFERRAL_INVITE_HOURS часов PRO сразу после
   того, как друг запустил бота по ссылке (database.award_referral_invite,
   идемпотентно через payment_ref="invite:<id>").
2. За оплату — +REFERRAL_REWARD_PCT% от подписки приглашенного днями PRO
   (30 дн. * 15% = 5 дней за оплаченный месяц; Stars и крипта,
   уникальный payment_ref — см. database.award_referral).

Про эти награды пишем ТОЛЬКО здесь, на экране /ref (и вложенном
«как это работает») — в приветствии, /help и профиле бота их нет.

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
from bot.database import ensure_referral_code, referral_stats
from bot.handlers.ui import edit_screen
from bot.keyboards import back_to_menu_kb, referral_kb
from bot.models import User

logger = logging.getLogger(__name__)

router = Router(name="referral")


def _referral_url(bot_username: str, payload: str) -> str:
    """Deep-link ``/start ref_<payload>`` — персональный код или (легаси) tg_id."""
    return f"https://t.me/{bot_username}?start=ref_{payload}"


def _fmt_earned(days: float) -> str:
    """Человекочитаемая сумма наград: ``5.0417`` -> «5 дн. 1 ч», ``0.125`` -> «3 ч»."""
    total_hours = round(days * 24)
    d, h = divmod(int(total_hours), 24)
    if d and h:
        return f"{d} дн. {h} ч"
    if d:
        return f"{d} дн."
    return f"{h} ч"


def _text(link: str, invited: int, earned_str: str, reward_days: float, invite_hours: float) -> str:
    return (
        "🤝 <b>Реферальная система</b>\n"
        "\n"
        f"🎁 За каждого друга, который запустит бота по твоей ссылке, — "
        f"<b>+{invite_hours:g} ч PRO</b> сразу.\n"
        f"💎 Каждый оплаченный месяц друга = <b>+{reward_days:g} дней</b> тебе, "
        "начисляется автоматически в момент оплаты.\n"
        "\n"
        f"<b>Твоя ссылка:</b>\n<code>{link}</code>\n"
        "\n"
        f"👥 <b>Приглашено:</b> {invited}\n"
        f"🏆 <b>Заработано:</b> {earned_str}\n"
        "\n"
        "Часы и дни суммируются с текущей подпиской — ничего подтверждать не нужно."
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

    code = await ensure_referral_code(session, db_user)
    link = _referral_url(bot_username, code or str(db_user.telegram_id))
    invited, earned = await referral_stats(session, db_user)
    reward_days = float(ceil(settings.PRO_MONTH_DAYS * settings.REFERRAL_REWARD_PCT / 100))

    text = _text(
        link, invited, _fmt_earned(earned), reward_days, float(settings.REFERRAL_INVITE_HOURS)
    )
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
        f"2️⃣ Друг жмет /start по ней — он закреплен за тобой навсегда, "
        f"а тебе сразу <b>+{settings.REFERRAL_INVITE_HOURS:g} ч PRO</b>.\n"
        f"3️⃣ Он оплачивает PRO (Stars или крипта) — тебе мгновенно начисляется "
        f"<b>+{settings.REFERRAL_REWARD_PCT}% от месяца</b> днями PRO.\n"
        "\n"
        "Начисление автоматическое и защищено от накрутки: один друг = один "
        "час, один платеж = одна награда, самореферал и повторные привязки "
        "не работают."
    )
    await edit_screen(callback, text, back_to_menu_kb())
    await callback.answer()


__all__ = ["router", "_referral_url"]
