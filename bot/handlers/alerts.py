"""/alerts — текстовое управление персональными триггерами из бота.

Просмотр списка и удаление; создание алертов выполняется в Mini App
(конструктор на экране 🔔 ALERTS).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import count_active_alerts
from bot.keyboards import alerts_list_kb, main_menu_kb
from bot.models import Alert, User

router = Router(name="alerts")


async def _load_alerts(session: AsyncSession, db_user: User) -> list[Alert]:
    result = await session.execute(
        select(Alert)
        .where(Alert.user_id == db_user.id, Alert.is_active.is_(True))
        .order_by(Alert.created_at.desc())
    )
    return list(result.scalars())


def _alerts_text(alerts: list[Alert], db_user: User, limit: int) -> str:
    if not alerts:
        return (
            "🔕 <b>Алертов пока нет</b>\n"
            "\n"
            "Создай триггер в терминале (экран 🔔 ALERTS):\n"
            "выбери подарок, максимальную цену и нужный дисконт —\n"
            "бот пришлет пуш, когда появится подходящий лот.\n"
            "\n"
            f"Тариф FREE: максимум <b>{limit}</b> активный алерт.\n"
            "PRO — безлимит и мгновенная доставка."
        )

    lines = ["🔔 <b>Мои алерты</b>\n"]
    for i, alert in enumerate(alerts, start=1):
        lines.append(
            f"{i}. 🎁 <b>{alert.gift_name}</b>\n"
            f"    ≤ <b>{alert.max_price:g} TON</b> · дисконт от "
            f"<b>−{alert.min_discount:g}%</b>\n"
            f"    <i>#{alert.id}</i>"
        )
    quota = "∞ (PRO)" if db_user.is_pro_active() else f"{len(alerts)}/{limit}"
    lines.append(f"\nАктивных алертов: <b>{quota}</b>")
    return "\n".join(lines)


@router.message(Command("alerts"))
async def cmd_alerts(message: Message, session: AsyncSession, db_user: User) -> None:
    """/alerts — показать активные триггеры."""
    settings = get_settings()
    alerts = await _load_alerts(session, db_user)
    await message.answer(
        _alerts_text(alerts, db_user, settings.FREE_ALERTS_LIMIT),
        reply_markup=alerts_list_kb(alerts) if alerts else main_menu_kb(settings.MINI_APP_URL),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "menu:alerts")
async def cb_menu_alerts(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Кнопка «Мои алерты» из главного меню."""
    settings = get_settings()
    if callback.message is None:
        await callback.answer()
        return
    alerts = await _load_alerts(session, db_user)
    await callback.message.answer(
        _alerts_text(alerts, db_user, settings.FREE_ALERTS_LIMIT),
        reply_markup=alerts_list_kb(alerts) if alerts else main_menu_kb(settings.MINI_APP_URL),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert_del:"))
async def cb_delete_alert(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Удаление алерта по кнопке «✕ …»."""
    settings = get_settings()
    raw_id = callback.data.split(":", maxsplit=1)[1]
    if not raw_id.isdigit() or callback.message is None:
        await callback.answer("Некорректный алерт", show_alert=True)
        return

    alert = await session.get(Alert, int(raw_id))
    if alert is None or alert.user_id != db_user.id:
        await callback.answer("Алерта уже не существует", show_alert=True)
        return

    await session.delete(alert)
    await session.flush()

    remaining = await _load_alerts(session, db_user)
    await callback.message.edit_text(
        _alerts_text(remaining, db_user, settings.FREE_ALERTS_LIMIT),
        reply_markup=alerts_list_kb(remaining) if remaining else None,
        disable_web_page_preview=True,
    )
    await callback.answer(f"Алерта «{alert.gift_name}» удален ✕")


@router.message(Command("alerts_quota"))
async def cmd_alerts_quota(message: Message, session: AsyncSession, db_user: User) -> None:
    """/alerts_quota — сколько алертов доступно."""
    settings = get_settings()
    used = await count_active_alerts(session, db_user)
    if db_user.is_pro_active():
        await message.answer(f"👑 PRO: использовано <b>{used}</b> алертов, лимит — безлимит.")
    else:
        await message.answer(
            f"FREE: использовано <b>{used}/{settings.FREE_ALERTS_LIMIT}</b> алертов.\n"
            "PRO снимает лимит: /pro"
        )


__all__ = ["router"]
