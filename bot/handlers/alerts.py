"""/alerts — экран персональных триггеров из бота.

Экран редактирует текущее сообщение (не плодит новые): список с
пагинацией, удаление, кнопка создания в Mini App и «В меню».
Создание алертов выполняется в Mini App (конструктор на экране 🔔 ALERTS).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import count_active_alerts
from bot.handlers.ui import edit_screen
from bot.keyboards import ALERTS_PAGE_SIZE, alerts_screen_kb, settings_kb
from bot.models import Alert, User

router = Router(name="alerts")


async def _load_alerts(session: AsyncSession, db_user: User) -> list[Alert]:
    result = await session.execute(
        select(Alert)
        .where(Alert.user_id == db_user.id, Alert.is_active.is_(True))
        .order_by(Alert.created_at.desc())
    )
    return list(result.scalars())


def _alerts_text(
    alerts: list[Alert], db_user: User, limit: int, page: int = 0
) -> str:
    total = len(alerts)
    pages = max(1, -(-total // ALERTS_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    window = alerts[page * ALERTS_PAGE_SIZE:(page + 1) * ALERTS_PAGE_SIZE]

    header = "🔔 <b>Мои алерты</b>"
    if pages > 1:
        header += f" · страница {page + 1}/{pages}"

    if not total:
        return (
            "🔕 <b>Алертов пока нет</b>\n"
            "\n"
            "Нажми «➕ Создать алерт в терминале»:\n"
            "выбери подарок, максимальную цену и нужный дисконт —\n"
            "бот пришлет пуш, когда появится подходящий лот.\n"
            "\n"
            f"Тариф FREE: максимум <b>{limit}</b> активный алерт.\n"
            "PRO — безлимит и мгновенная доставка."
        )

    lines = [header, ""]
    for i, alert in enumerate(window, start=page * ALERTS_PAGE_SIZE + 1):
        lines.append(
            f"{i}. 🎁 <b>{alert.gift_name}</b>\n"
            f"    ≤ <b>{alert.max_price:g} TON</b> · дисконт от "
            f"<b>−{alert.min_discount:g}%</b>\n"
            f"    <i>#{alert.id}</i>"
        )
    quota = "∞ (PRO)" if db_user.is_pro_active() else f"{total}/{limit}"
    lines.append(f"\nАктивных алертов: <b>{quota}</b>")
    lines.append("Кнопка «✕ …» удаляет алерт.")
    return "\n".join(lines)


async def _show_alerts_screen(
    callback: CallbackQuery, session: AsyncSession, db_user: User, page: int = 0
) -> None:
    """Рендерит экран алертов редактированием текущего сообщения."""
    settings = get_settings()
    alerts = await _load_alerts(session, db_user)
    pages = max(1, -(-len(alerts) // ALERTS_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    await edit_screen(
        callback,
        _alerts_text(alerts, db_user, settings.FREE_ALERTS_LIMIT, page),
        alerts_screen_kb(alerts, page, settings.MINI_APP_URL),
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message, session: AsyncSession, db_user: User) -> None:
    """/alerts — экран триггеров новым сообщением (это точка входа)."""
    settings = get_settings()
    alerts = await _load_alerts(session, db_user)
    await message.answer(
        _alerts_text(alerts, db_user, settings.FREE_ALERTS_LIMIT),
        reply_markup=alerts_screen_kb(alerts, 0, settings.MINI_APP_URL),
    )


@router.callback_query(F.data == "menu:alerts")
async def cb_menu_alerts(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """Кнопка «Мои алерты» — переключает экран, а не шлёт новое сообщение."""
    await _show_alerts_screen(callback, session, db_user, page=0)
    await callback.answer()


@router.callback_query(F.data.startswith("alerts:page:"))
async def cb_alerts_page(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """◀/▶ — пагинация списка алертов."""
    raw = callback.data.rsplit(":", maxsplit=1)[1]
    page = int(raw) if raw.isdigit() else 0
    await _show_alerts_screen(callback, session, db_user, page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("alert_del:"))
async def cb_delete_alert(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """Удаление алерта кнопкой «✕ …» и перерисовка той же страницы.

    Формат кнопки: ``alert_del:<id>:<page>``; старые кнопки из истории
    чата шлют ``alert_del:<id>`` — тоже принимаем (страница 0).
    """
    parts = callback.data.split(":")
    raw_id = parts[1] if len(parts) > 1 else ""
    raw_page = parts[2] if len(parts) > 2 else "0"
    page = int(raw_page) if raw_page.isdigit() else 0

    if not raw_id.isdigit():
        await callback.answer("Некорректный алерт", show_alert=True)
        return

    alert = await session.get(Alert, int(raw_id))
    if alert is None or alert.user_id != db_user.id:
        await callback.answer("Алерта уже не существует", show_alert=True)
        return

    gift_name = alert.gift_name
    await session.delete(alert)
    await session.flush()
    await _show_alerts_screen(callback, session, db_user, page=page)
    await callback.answer(f"Алерта «{gift_name}» удален ✕")


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


SETTINGS_TEXT = (
    "🎚 <b>PRO-фильтр дисконта</b>\n"
    "\n"
    "Выбери минимальный дисконт для broadcast-сигналов.\n"
    "Сигналы слабее порога приходить не будут — меньше шума, "
    "только жирные лоты.\n"
    "\n"
    "Текущий порог: <b>{current}</b>\n"
    "\n"
    "Персональные алерты работают по своему порогу и этот фильтр не затрагивает."
)


def _settings_text(user: User) -> str:
    value = float(user.min_discount or 0.0)
    current = f"от −{value:g}%" if value > 0 else "выключен"
    if not user.is_pro_active():
        current += " (фильтр включится после покупки PRO)"
    return SETTINGS_TEXT.format(current=current)


@router.message(Command("settings"))
async def cmd_settings(message: Message, db_user: User) -> None:
    """/settings — порог дисконта PRO-фильтра."""
    await message.answer(_settings_text(db_user), reply_markup=settings_kb(float(db_user.min_discount or 0)))


@router.callback_query(F.data.startswith("settings:disc:"))
async def cb_settings_discount(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """Устанавливает порог дисконта (применяется только при активном PRO)."""
    raw = callback.data.rsplit(":", maxsplit=1)[1]
    if not raw.isdigit():
        await callback.answer("Некорректное значение", show_alert=True)
        return

    value = float(raw)
    db_user.min_discount = value
    await session.flush()

    if callback.message is not None:
        await callback.message.edit_text(
            _settings_text(db_user),
            reply_markup=settings_kb(value),
        )

    if db_user.is_pro_active():
        await callback.answer(f"Порог: от −{value:g}%" if value > 0 else "Фильтр выключен")
    else:
        await callback.answer("Сохранено — включится после покупки PRO", show_alert=True)


__all__ = ["router"]
