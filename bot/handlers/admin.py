"""/admin — статистика проекта и рассылка всем пользователям.

Доступ только для ADMIN_IDS из .env. Рассылка построена на FSM:
admin:broadcast -> бот ждет сообщение -> копирует его всем пользователям
с троттлингом BROADCAST_DELAY_SEC.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.keyboards import admin_panel_kb, cancel_kb
from bot.models import Alert, Payment, SnipeSignal, User
from bot.services.notifier import Notifier

logger = logging.getLogger(__name__)

router = Router(name="admin")


class BroadcastFSM(StatesGroup):
    """Состояния сценария рассылки."""

    waiting_message = State()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS


# ----------------------------------------------------------------------
# Вход в панель
# ----------------------------------------------------------------------
@router.message(Command("admin"))
async def cmd_admin(message: Message, db_user: User) -> None:
    """/admin — главная админ-панель."""
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return
    await message.answer("🛠 <b>Админ-панель GiftTracker</b>\nВыбери действие:", reply_markup=admin_panel_kb())


@router.callback_query(F.data == "admin:cancel")
async def cb_admin_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена сценария рассылки."""
    await state.clear()
    if callback.message is not None:
        await callback.message.answer("Рассылка отменена ✕")
    await callback.answer()


# ----------------------------------------------------------------------
# Статистика
# ----------------------------------------------------------------------
@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Сводная статистика: пользователи, PRO, доход в Stars, сигналы."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
    pro_users = (
        await session.execute(select(func.count(User.id)).where(User.is_pro.is_(True)))
    ).scalar_one()
    active_alerts = (
        await session.execute(select(func.count(Alert.id)).where(Alert.is_active.is_(True)))
    ).scalar_one()
    signals_total = (await session.execute(select(func.count(SnipeSignal.id)))).scalar_one()

    stars_total = (
        await session.execute(select(func.coalesce(func.sum(Payment.stars_amount), 0)))
    ).scalar_one()
    month_stars = (
        await session.execute(
            select(func.coalesce(func.sum(Payment.stars_amount), 0)).where(Payment.plan == "pro_month")
        )
    ).scalar_one()
    lifetime_stars = (
        await session.execute(
            select(func.coalesce(func.sum(Payment.stars_amount), 0)).where(Payment.plan == "pro_lifetime")
        )
    ).scalar_one()
    payments_count = (await session.execute(select(func.count(Payment.id)))).scalar_one()

    text = (
        "📊 <b>Статистика GiftTracker</b>\n"
        "\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"👑 PRO-подписок: <b>{pro_users}</b>\n"
        f"🔔 Активных алертов: <b>{active_alerts}</b>\n"
        f"⚡ Сигналов найдено: <b>{signals_total}</b>\n"
        "\n"
        f"⭐ Доход: <b>{stars_total} Stars</b> (~${stars_total * 0.02:.2f})\n"
        f"   • месячные: {month_stars}★\n"
        f"   • lifetime: {lifetime_stars}★\n"
        f"   • платежей всего: {payments_count}"
    )
    await callback.message.edit_text(text, reply_markup=admin_panel_kb())
    await callback.answer()


# ----------------------------------------------------------------------
# Рассылка (FSM)
# ----------------------------------------------------------------------
@router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    """Старт сценария рассылки: ждем сообщение для отправки."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    await state.set_state(BroadcastFSM.waiting_message)
    if callback.message is not None:
        await callback.message.answer(
            "📣 Пришли сообщение, которое нужно разослать всем пользователям "
            "(текст/фото/видео — будет скопировано как есть).",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(BroadcastFSM.waiting_message, Command("cancel"))
async def cmd_cancel_broadcast(message: Message, state: FSMContext) -> None:
    """/cancel во время рассылки."""
    await state.clear()
    await message.answer("Рассылка отменена ✕")


@router.message(BroadcastFSM.waiting_message)
async def process_broadcast(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    notifier: Notifier,
) -> None:
    """Копирует сообщение админа всем пользователям с троттлингом."""
    if message.bot is None:
        await state.clear()
        await message.answer("⚠️ Рассылка недоступна, попробуй позже.")
        return

    users = list((await session.execute(select(User))).scalars())
    await state.clear()
    await message.answer(f"📣 Рассылка запущена: {len(users)} получателей…")

    sent = await notifier.broadcast_admin_message(
        bot=message.bot,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        users=users,
    )
    await message.answer(f"✅ Рассылка завершена: доставлено <b>{sent}/{len(users)}</b>.")


__all__ = ["router", "BroadcastFSM"]
