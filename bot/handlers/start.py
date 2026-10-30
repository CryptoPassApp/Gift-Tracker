"""/start — приветствие, главное меню и кнопка запуска Mini App.

Навигация: инлайн-кнопки редактируют текущее сообщение (ui.edit_screen),
поэтому меню не плодит дубликаты в чате. Новые сообщения шлют только
команды.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.handlers.ui import edit_screen
from bot.keyboards import back_to_menu_kb, main_menu_kb, support_kb
from bot.models import User

router = Router(name="start")

WELCOME_TEXT = (
    "⚡ <b>GIFTTRACKER — терминал подарков Telegram</b>\n"
    "Ловим сделки ниже флора по всем площадкам.\n"
    "\n"
    "🔎 <b>Что внутри:</b>\n"
    "• <b>Лента сделок</b> — лоты Getgems и Portals дешевле флора модели, "
    "с чистым профитом после комиссий маркетов\n"
    "• <b>Флоры 122 коллекций</b> — по каждой модели, с изменением за 24ч\n"
    "• <b>Flip-калькулятор</b> — профит конкретной сделки с учетом комиссии и газа\n"
    "• <b>Алерты</b> — триггер по цене/дисконту на любой подарок\n"
    "\n"
    "👑 <b>PRO</b> — мгновенные пуши (0 сек вместо 45) и безлимит алертов.\n"
    "Оплата: Telegram Stars или крипта (@CryptoBot).\n"
    "🤝 <b>Реферальная система</b> — дни PRO за приглашенных друзей (/ref).\n"
    "\n"
    "Жми кнопку ниже — терминал откроется прямо в Telegram 👇"
)

HOW_IT_WORKS_TEXT = (
    "❓ <b>Как это работает</b>\n"
    "\n"
    "1️⃣ Радар каждые 5 секунд обходит лоты на Getgems и Portals "
    "(122 коллекции подарков).\n"
    "2️⃣ Каждый лот сравнивается с флором его модели — база честная, "
    "не «средняя по коллекции».\n"
    "3️⃣ Считается чистый профит: <code>Floor × 0.95 − Deal − Gas</code> "
    "(комиссия площадки и газ уже внутри).\n"
    "4️⃣ Лоты дешевле флора попадают в ленту терминала; от −30% и ниже — "
    "мгновенный пуш с фото лота.\n"
    "5️⃣ PRO получает сигнал <b>в ту же секунду</b>, FREE — через 45 секунд.\n"
    "\n"
    "Оплата PRO: ⭐ Stars или 💳 крипта через @CryptoBot. "
    "За приглашенных друзей — +15% их подписки днями PRO (/ref)."
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, db_user: User) -> None:
    """/start — приветствие + главное меню; ``?start=ref_<id>`` — реферал."""
    settings = get_settings()
    await _apply_referral(message, session, db_user)
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_kb(
            settings.MINI_APP_URL,
            is_admin=db_user.telegram_id in settings.ADMIN_IDS,
        ),
        disable_web_page_preview=True,
    )


async def _apply_referral(message: Message, session: AsyncSession, db_user: User) -> None:
    """Запоминает пригласившего из deep-link ``/start ref_<telegram_id>``."""
    text = message.text or ""
    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if not payload.startswith("ref_") or db_user.referred_by:
        return
    try:
        referrer_tg = int(payload[4:])
    except ValueError:
        return
    if referrer_tg == db_user.telegram_id:
        return
    referrer = (
        await session.execute(select(User).where(User.telegram_id == referrer_tg))
    ).scalar_one_or_none()
    if referrer is None:
        return
    db_user.referred_by = referrer.id
    await session.flush()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """/help — как работает снайпер."""
    await message.answer(HOW_IT_WORKS_TEXT, disable_web_page_preview=True)


MENU_TEXT = "⚡ <b>Главное меню</b>\nВыбирай раздел:"


@router.callback_query(F.data == "menu:start")
async def cb_menu_start(callback: CallbackQuery, db_user: User) -> None:
    """Возврат в главное меню — редактированием текущего сообщения."""
    settings = get_settings()
    await edit_screen(
        callback,
        MENU_TEXT,
        main_menu_kb(
            settings.MINI_APP_URL,
            is_admin=db_user.telegram_id in settings.ADMIN_IDS,
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    """Служебная кнопка-счётчик (например, «2/5» пагинации) без действия."""
    await callback.answer()


@router.callback_query(F.data == "menu:how")
async def cb_how_it_works(callback: CallbackQuery) -> None:
    """Кнопка «Как это работает» — экран с возвратом в меню."""
    await edit_screen(callback, HOW_IT_WORKS_TEXT, back_to_menu_kb())
    await callback.answer()


SUPPORT_TEXT = (
    "🛠 <b>Тех-поддержка</b>\n"
    "\n"
    "Есть вопрос или проблема?\n"
    "• Не приходит пуш / пропала сделка\n"
    "• Оплата Stars или криптой\n"
    "• Реферальные начисления\n"
    "\n"
    "Напиши нам — отвечаем по мере поступления."
)


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery) -> None:
    """Кнопка «Тех-поддержка» — экран с переходом в саппорт-чат."""
    settings = get_settings()
    await edit_screen(
        callback,
        SUPPORT_TEXT,
        support_kb(settings.SUPPORT_URL),
    )
    await callback.answer()


@router.message(Command("top"))
async def cmd_top(message: Message, session: AsyncSession) -> None:
    """/top — топ-5 самых жирных сигналов за все время."""
    from bot.models import SnipeSignal

    result = await session.execute(
        select(SnipeSignal).order_by(SnipeSignal.discount_pct.desc()).limit(5)
    )
    top = list(result.scalars())

    if not top:
        await message.answer("📊 Ради еще не поймал ни одного лота — загляни через минуту.")
        return

    lines = ["🏆 <b>Топ-5 снайпов радара:</b>\n"]
    for i, sig in enumerate(top, start=1):
        lines.append(
            f"{i}. 🎁 <b>{sig.gift_name}</b> {sig.lot_number} — "
            f"<b>−{sig.discount_pct}%</b> "
            f"(+{sig.net_profit_ton} TON net)"
        )
    lines.append("\nОткрывай терминал и забирай следующие 👇")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


__all__ = ["router"]
