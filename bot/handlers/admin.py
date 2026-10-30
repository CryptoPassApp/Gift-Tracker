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
from bot.handlers.ui import edit_screen
from bot.keyboards import admin_panel_kb, cancel_kb
from bot.models import Alert, Payment, SnipeSignal, User
from bot.services.admin_stats import (
    grant_pro,
    normalize_manual_plan,
    recent_payments,
    revenue_by_day,
    revenue_by_plan,
)
from bot.services.notifier import Notifier

logger = logging.getLogger(__name__)

router = Router(name="admin")


class BroadcastFSM(StatesGroup):
    """Состояния сценария рассылки."""

    waiting_message = State()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS


_GRANT_USAGE = (
    "👤 <b>Ручная выдача PRO</b>\n"
    "Формат: <code>/grant &lt;telegram_id&gt; &lt;month|lifetime&gt;</code>\n"
    "Пример: <code>/grant 1419114126 month</code>\n"
    "Если пользователь еще не писал боту, запись будет создана автоматически."
)


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


@router.callback_query(F.data == "admin:panel")
async def cb_admin_panel(callback: CallbackQuery, db_user: User) -> None:
    """Кнопка «🛠 Админка» в главном меню — панель открывается на месте.

    Кнопка рисуется только админам, но проверку дублируем: callback из
    старого сообщения мог остаться в чате у другого пользователя.
    """
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    await edit_screen(
        callback,
        "🛠 <b>Админ-панель GiftTracker</b>\nВыбери действие:",
        admin_panel_kb(),
    )
    await callback.answer()


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

    by_plan = await revenue_by_plan(session)
    daily = await revenue_by_day(session, days=7)
    latest = await recent_payments(session, limit=5)

    stars_total = sum(plan["stars"] for plan in by_plan.values())
    payments_count = sum(plan["payments"] for plan in by_plan.values())
    month_stars = by_plan["pro_month"]["stars"]
    lifetime_stars = by_plan["pro_lifetime"]["stars"]

    daily_lines = "\n".join(
        f"   • {row['day']}: {row['payments']} пл. / {row['stars']}★" for row in daily
    ) or "   • платежей пока нет"

    payment_lines = []
    for payment in latest:
        username = payment.user.username if payment.user else None
        who = f"@{username}" if username else f"id {payment.user.telegram_id if payment.user else '?'}"
        payment_lines.append(
            f"   • {payment.created_at.strftime('%d.%m %H:%M')} {who} — "
            f"{payment.stars_amount}★ ({payment.plan})"
        )
    recent_text = "\n".join(payment_lines) or "   • платежей пока нет"

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
        f"   • платежей всего: {payments_count}\n"
        "\n"
        "📈 <b>Доход по дням (7д):</b>\n"
        f"{daily_lines}\n"
        "\n"
        "🧾 <b>Последние платежи:</b>\n"
        f"{recent_text}\n"
        "\n"
        "👤 Выдать PRO вручную: /grant &lt;telegram_id&gt; &lt;month|lifetime&gt;"
    )
    await callback.message.edit_text(text, reply_markup=admin_panel_kb())
    await callback.answer()


# ----------------------------------------------------------------------
# Ручная выдача PRO
# ----------------------------------------------------------------------
@router.callback_query(F.data == "admin:grant")
async def cb_admin_grant(callback: CallbackQuery, db_user: User) -> None:
    """Кнопка «Выдать PRO»: показывает формат команды."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    if callback.message is not None:
        await callback.message.answer(_GRANT_USAGE)
    await callback.answer()


@router.message(Command("grant"))
async def cmd_grant(message: Message, session: AsyncSession, db_user: User) -> None:
    """``/grant <telegram_id> <month|lifetime>`` — ручная выдача/продление PRO."""
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer(_GRANT_USAGE)
        return

    telegram_id = int(parts[1])
    settings = get_settings()
    try:
        normalized = normalize_manual_plan(parts[2])
        user = await grant_pro(
            session, telegram_id, normalized, month_days=settings.PRO_MONTH_DAYS
        )
    except ValueError:
        await message.answer(_GRANT_USAGE)
        return

    if user.pro_expires_at is not None and user.pro_expires_at.year == 9999:
        expires = "навсегда 👑"
    elif user.pro_expires_at is not None:
        expires = user.pro_expires_at.strftime("%d.%m.%Y")
    else:
        expires = "—"
    plan_label = "Lifetime" if normalized == "pro_lifetime" else f"{settings.PRO_MONTH_DAYS} дней"

    await message.answer(
        "✅ <b>PRO выдан</b>\n"
        f"👤 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"📦 Тариф: <b>{plan_label}</b>\n"
        f"⏳ Действует: <b>{expires}</b>"
    )
    logger.info(
        "Admin %s granted PRO: user=%s plan=%s",
        db_user.telegram_id,
        telegram_id,
        normalized,
    )


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
