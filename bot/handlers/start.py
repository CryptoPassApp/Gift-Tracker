"""/start — приветствие, главное меню и кнопка запуска Mini App."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.keyboards import main_menu_kb
from bot.models import User

router = Router(name="start")

WELCOME_TEXT = (
    "⚡ <b>GIFTTRACKER — Sniper Terminal</b>\n"
    "Ловим подарки Telegram ниже флора, пока другие спят.\n"
    "\n"
    "🔥 <b>Что умеет терминал:</b>\n"
    "• <b>Live-лента снайпера</b> — лоты с дисконтом ≥15% от флора в реальном времени\n"
    "• <b>Hot Liquidation</b> — мгновенный пуш при −30% и ниже\n"
    "• <b>Floor Matrix</b> — флоры 8 коллекций в TON/USD и статус ликвидности\n"
    "• <b>Flip-калькулятор</b> — чистая прибыль с учетом комиссии 5% и газа\n"
    "• <b>Персональные алерты</b> — триггеры по цене и дисконту\n"
    "\n"
    "💰 <b>Математика снайпа:</b>\n"
    "<code>Profit = Floor × 0.95 − Deal − 0.08 TON</code>\n"
    "\n"
    "👑 <b>PRO-снайперы</b> получают пуши <b>мгновенно</b> (0 сек вместо 45) "
    "и безлимитные алерты.\n"
    "\n"
    "Жми кнопку ниже — терминал откроется прямо в Telegram 👇"
)

HOW_IT_WORKS_TEXT = (
    "❓ <b>Как это работает</b>\n"
    "\n"
    "1️⃣ Радар каждые 5 секунд сканирует лоты подарков Telegram.\n"
    "2️⃣ Движок считает дисконт и чистую прибыль по формуле "
    "<code>(Floor × 0.95) − Deal − 0.08</code>.\n"
    "3️⃣ Лоты с дисконтом ≥15% попадают в ленту; ≥30% — пушатся как "
    "<b>Hot Liquidation</b>.\n"
    "4️⃣ PRO получает сигнал <b>в ту же секунду</b>, FREE — через 45 секунд.\n"
    "\n"
    "Именно эти 45 секунд решают, кто заберет лот −34%, а кто увидит "
    "«уже продано» 😉"
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, db_user: User) -> None:
    """/start — приветствие + главное меню с кнопкой Mini App."""
    settings = get_settings()
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_kb(settings.MINI_APP_URL),
        disable_web_page_preview=True,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """/help — как работает снайпер."""
    await message.answer(HOW_IT_WORKS_TEXT, disable_web_page_preview=True)


@router.callback_query(F.data == "menu:start")
async def cb_menu_start(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Возврат в главное меню по инлайн-кнопке."""
    settings = get_settings()
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.answer(
        "⚡ <b>Главное меню</b>\nВыбирай раздел:",
        reply_markup=main_menu_kb(settings.MINI_APP_URL),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:how")
async def cb_how_it_works(callback: CallbackQuery) -> None:
    """Кнопка «Как это работает»."""
    await callback.message.answer(HOW_IT_WORKS_TEXT, disable_web_page_preview=True)
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
