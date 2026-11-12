"""/admin — статистика проекта и рассылка всем пользователям.

Доступ только для ADMIN_IDS из .env. Рассылка построена на FSM:
admin:broadcast -> бот ждет сообщение -> копирует его всем пользователям
с троттлингом BROADCAST_DELAY_SEC.
"""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.handlers.support import admin_panel_with_badge
from bot.handlers.ui import edit_screen
from bot.keyboards import cancel_kb
from bot.models import Alert, Payment, SnipeSignal, User
from bot.services.admin_stats import (
    active_pro_users,
    ban_user,
    grant_pro,
    grant_pro_days,
    normalize_manual_plan,
    recent_payments,
    revenue_by_day,
    revenue_by_plan,
    revoke_pro,
    unban_user,
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
    "Формат: <code>/grant &lt;telegram_id&gt; &lt;month|lifetime|дней&gt;</code>\n"
    "Примеры:\n"
    "<code>/grant 123456789 month</code> — месяц\n"
    "<code>/grant 123456789 10</code> — 10 дней\n"
    "<code>/grant 123456789 0.5</code> — 12 часов\n"
    "Забрать PRO: <code>/revoke &lt;telegram_id&gt;</code>\n"
    "Если пользователь еще не писал боту, запись будет создана автоматически."
)

_REVOKE_USAGE = (
    "🚫 <b>Отзыв PRO</b>\n"
    "Формат: <code>/revoke &lt;telegram_id&gt;</code>\n"
    "Снимает PRO и обнуляет срок — в том числе у lifetime.\n"
    "Работает только по тем, кто хотя бы раз писал боту."
)

_BAN_USAGE = (
    "⛔ <b>Бан пользователя</b>\n"
    "Формат: <code>/ban &lt;telegram_id&gt; &lt;причина&gt;</code>\n"
    "Пример: <code>/ban 123456789 Спам и попытка скама</code>\n"
    "Пользователь получает сообщение с причиной, его сообщения\n"
    "и запросы к Mini App отсекаются, пуши не доставляются.\n"
    "Разбан: <code>/unban &lt;telegram_id&gt;</code>"
)

_UNBAN_USAGE = (
    "✅ <b>Разбан</b>\n"
    "Формат: <code>/unban &lt;telegram_id&gt;</code>\n"
    "Снимает бан, возвращает доступ к боту и Mini App."
)


def _human_days(days: float) -> str:
    """«30 дней» / «1 день» / «12 ч» — человекочитаемый срок для ответа."""
    if days == int(days):
        n = int(days)
        if n == 1:
            return "1 день"
        if 2 <= n <= 4:
            return f"{n} дня"
        return f"{n} дней"
    hours = days * 24
    if abs(hours - round(hours)) < 1e-9:
        return f"{round(hours)} ч"
    return f"{days:g} дн."


def _esc(text: str | None) -> str:
    """HTML-экранирование пользовательского текста для parse_mode=HTML.

    Без экранирования `<` в причине бана/сообщении рвет разметку:
    TelegramBadRequest → исключение в хендлере → откат сессии БД,
    и сам бан/выдача PRO теряются вместе с ответом админу.
    """
    return html.escape(text or "", quote=False)


# ----------------------------------------------------------------------
# Вход в панель
# ----------------------------------------------------------------------
@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession, db_user: User) -> None:
    """/admin — главная админ-панель."""
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return
    kb = await admin_panel_with_badge(session)
    await message.answer(
        "🛠 <b>Админ-панель GiftTracker</b>\nВыбери действие:", reply_markup=kb
    )


@router.callback_query(F.data == "admin:panel")
async def cb_admin_panel(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
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
        await admin_panel_with_badge(session),
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
        "👤 Выдать PRO: /grant &lt;telegram_id&gt; &lt;month|lifetime|дней&gt;\n"
        "🚫 Забрать PRO: /revoke &lt;telegram_id&gt;"
    )
    # На экране статистики кнопка «Статистика» не нужна (она и открыла экран).
    await callback.message.edit_text(
        text, reply_markup=await admin_panel_with_badge(session, include_stats=False)
    )
    await callback.answer()


@router.callback_query(F.data == "admin:pro_list")
async def cb_admin_pro_list(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Текстовый список активных PRO-подписчиков: юз + telegram_id + срок."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    users = await active_pro_users(session)
    if not users:
        text = "👑 <b>PRO-подписчики</b>\n\nАктивных подписок сейчас нет."
    else:
        lines = []
        for i, u in enumerate(users, 1):
            who = f"@{u.username}" if u.username else "без юза"
            if u.pro_expires_at is None or u.pro_expires_at.year == 9999:
                expires = "навсегда"
            else:
                expires = u.pro_expires_at.strftime("%d.%m.%Y")
            lines.append(f"{i}. {who} · <code>{u.telegram_id}</code> · до {expires}")
        text = f"👑 <b>PRO-подписчики</b> ({len(users)}):\n\n" + "\n".join(lines[:80])
        if len(users) > 80:
            text += f"\n… и ещё {len(users) - 80}"

    await callback.message.edit_text(
        text, reply_markup=await admin_panel_with_badge(session, include_pro_list=False)
    )
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


@router.callback_query(F.data == "admin:revoke")
async def cb_admin_revoke(callback: CallbackQuery, db_user: User) -> None:
    """Кнопка «Отозвать PRO»: показывает формат команды."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    if callback.message is not None:
        await callback.message.answer(_REVOKE_USAGE)
    await callback.answer()


@router.message(Command("grant"))
async def cmd_grant(message: Message, session: AsyncSession, db_user: User) -> None:
    """``/grant <telegram_id> <month|lifetime|дней>`` — выдача/продление PRO.

    Числовой аргумент — произвольный срок в днях (дробный = часы:
    ``0.5`` = 12 часов). Продление суммируется с активной подпиской.
    """
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer(_GRANT_USAGE)
        return

    telegram_id = int(parts[1])
    settings = get_settings()
    arg = parts[2].strip().lower()
    try:
        if re.fullmatch(r"\d+(?:[.,]\d+)?", arg):
            # Числовой срок: любое количество дней, дробное — в часах.
            days = float(arg.replace(",", "."))
            if days <= 0:
                raise ValueError("days must be positive")
            user = await grant_pro_days(session, telegram_id, days)
            plan_label = _human_days(days)
        else:
            normalized = normalize_manual_plan(arg)
            user = await grant_pro(
                session, telegram_id, normalized, month_days=settings.PRO_MONTH_DAYS
            )
            plan_label = (
                "Lifetime" if normalized == "pro_lifetime" else f"{settings.PRO_MONTH_DAYS} дней"
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
        plan_label,
    )

    # Сообщаем получателю: PRO мог быть выдан, пока он не в чате с админом.
    try:
        await message.bot.send_message(
            user.telegram_id,
            "👑 <b>Вам выдали доступ к PRO!</b>\n"
            f"📦 Тариф: <b>{plan_label}</b>\n"
            f"⏳ Действует: <b>{expires}</b>\n"
            "\n"
            "⚡ Пуши теперь приходят мгновенно, алерты — без лимита.",
        )
    except Exception:  # noqa: BLE001 — уведомление не ломает выдачу
        logger.warning("Grant notify failed for %s", user.telegram_id, exc_info=True)


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, session: AsyncSession, db_user: User) -> None:
    """``/revoke <telegram_id>`` — забрать PRO (в том числе lifetime)."""
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(_REVOKE_USAGE)
        return

    telegram_id = int(parts[1])
    try:
        user, previous_expires = await revoke_pro(session, telegram_id)
    except ValueError as exc:
        await message.answer(f"⚠️ Не получилось: {exc}.")
        return

    if previous_expires is not None and previous_expires.year == 9999:
        was = "навсегда"
    elif previous_expires is not None:
        was = previous_expires.strftime("%d.%m.%Y")
    else:
        was = "—"

    await message.answer(
        "🚫 <b>PRO отозван</b>\n"
        f"👤 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"📅 Действовал до: <b>{was}</b>\n"
        "Теперь для бота он FREE-пользователь."
    )
    logger.info(
        "Admin %s revoked PRO: user=%s (was until %s)",
        db_user.telegram_id,
        telegram_id,
        was,
    )

    # Уведомляем пользователя, чтобы не гадал, почему пропали пуши.
    try:
        await message.bot.send_message(
            user.telegram_id,
            "⚠️ <b>Доступ к PRO был отозван админом.</b>\n"
            "Пуши снова приходят с задержкой, алерты — с лимитом.\n"
            "Продлить можно командой /pro.",
        )
    except Exception:  # noqa: BLE001 — уведомление не ломает отзыв
        logger.warning("Revoke notify failed for %s", user.telegram_id, exc_info=True)


# ----------------------------------------------------------------------
# Бан / разбан
# ----------------------------------------------------------------------
@router.callback_query(F.data == "admin:ban")
async def cb_admin_ban(callback: CallbackQuery, db_user: User) -> None:
    """Кнопка «Бан / Разбан»: показывает формат команд."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    if callback.message is not None:
        await callback.message.answer(_BAN_USAGE)
    await callback.answer()


@router.message(Command("ban"))
async def cmd_ban(message: Message, session: AsyncSession, db_user: User) -> None:
    """``/ban <telegram_id> <причина>`` — блокировка доступа к боту и Mini App."""
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(_BAN_USAGE)
        return

    telegram_id = int(parts[1])
    reason = parts[2].strip() if len(parts) > 2 else "нарушение правил"
    try:
        user, was_banned = await ban_user(session, telegram_id, reason)
    except ValueError as exc:
        await message.answer(f"⚠️ Не получилось: {_esc(str(exc))}.")
        return

    ban_reply = (
        "⛔ <b>Пользователь забанен</b>\n"
        f"👤 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"📝 Причина: <b>{_esc(user.ban_reason)}</b>\n"
        + (
            "Бан продлен/обновлен."
            if was_banned
            else "Сообщения и пуши ему больше не доставляются.\n"
            "Разбан: /unban &lt;telegram_id&gt;"
        )
    )
    # Ответ админу не должен ломать уже примененный бан: любая ошибка
    # отправки здесь откатила бы сессию вместе с записью в БД.
    try:
        await message.answer(ban_reply)
    except Exception:  # noqa: BLE001 — бан уже применен, ответ админу вторичен
        logger.exception("Ban confirmation failed for user=%s", telegram_id)
    logger.info(
        "Admin %s banned user=%s reason=%s (was_banned=%s)",
        db_user.telegram_id,
        telegram_id,
        user.ban_reason,
        was_banned,
    )

    # Сообщаем причину — чтобы человек знал, за что.
    try:
        await message.bot.send_message(
            user.telegram_id,
            "⛔ <b>Вы заблокированы в боте.</b>\n"
            f"Причина: «{_esc(user.ban_reason)}»\n"
            "\n"
            "Если считаете это ошибкой — напишите администратору.",
        )
    except Exception:  # noqa: BLE001 — уведомление не ломает бан
        logger.warning("Ban notify failed for %s", user.telegram_id, exc_info=True)


@router.message(Command("unban"))
async def cmd_unban(message: Message, session: AsyncSession, db_user: User) -> None:
    """``/unban <telegram_id>`` — снять бан, вернуть доступ."""
    if not _is_admin(db_user.telegram_id):
        await message.answer("⛔ Раздел только для администраторов.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(_UNBAN_USAGE)
        return

    telegram_id = int(parts[1])
    try:
        user, previous_reason = await unban_user(session, telegram_id)
    except ValueError as exc:
        await message.answer(f"⚠️ Не получилось: {_esc(str(exc))}.")
        return

    unban_reply = (
        "✅ <b>Бан снят</b>\n"
        f"👤 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"📝 Была причина: <b>{_esc(previous_reason) or '—'}</b>\n"
        "Доступ к боту и Mini App восстановлен."
    )
    # Разбан уже применен — ошибка отправки не должна его откатить.
    try:
        await message.answer(unban_reply)
    except Exception:  # noqa: BLE001 — разбан уже применен, ответ админу вторичен
        logger.exception("Unban confirmation failed for user=%s", telegram_id)
    logger.info(
        "Admin %s unbanned user=%s (was: %s)",
        db_user.telegram_id,
        telegram_id,
        previous_reason,
    )

    try:
        await message.bot.send_message(
            user.telegram_id,
            "✅ <b>Вы разблокированы.</b>\n"
            "Доступ к боту и Mini App восстановлен — добро пожаловать обратно!",
        )
    except Exception:  # noqa: BLE001 — уведомление не ломает разбан
        logger.warning("Unban notify failed for %s", user.telegram_id, exc_info=True)


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

    users = [
        u
        for u in (await session.execute(select(User))).scalars()
        if not getattr(u, "is_banned", False)  # забаненные рассылки не получают
    ]
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
