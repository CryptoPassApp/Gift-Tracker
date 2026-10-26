"""Инлайн-клавиатуры бота: меню, тарифы PRO, алерты, админ-панель."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.models import Alert


def main_menu_kb(mini_app_url: str | None) -> InlineKeyboardMarkup:
    """Главное меню: запуск Mini App + разделы."""
    builder = InlineKeyboardBuilder()
    if mini_app_url:
        builder.row(
            InlineKeyboardButton(
                text="⚡ Открыть Sniper Terminal",
                web_app=WebAppInfo(url=mini_app_url),
            )
        )
    builder.row(
        InlineKeyboardButton(text="🔔 Мои алерты", callback_data="menu:alerts"),
        InlineKeyboardButton(text="👑 Купить PRO", callback_data="menu:pro"),
    )
    return builder.as_markup()


def pro_plans_kb() -> InlineKeyboardMarkup:
    """Выбор тарифа PRO (оплата Telegram Stars)."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ PRO месяц — 150★", callback_data="pro:month"))
    builder.row(InlineKeyboardButton(text="👑 PRO навсегда — 999★", callback_data="pro:lifetime"))
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def alerts_list_kb(alerts: list[Alert]) -> InlineKeyboardMarkup:
    """Список алертов с кнопками удаления (максимум 30 кнопок Telegram)."""
    builder = InlineKeyboardBuilder()
    for alert in alerts[:30]:
        builder.row(
            InlineKeyboardButton(
                text=f"✕ {alert.gift_name} ≤ {alert.max_price:g} TON (−{alert.min_discount:g}%)",
                callback_data=f"alert_del:{alert.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def admin_panel_kb() -> InlineKeyboardMarkup:
    """Админ-панель."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"))
    builder.row(InlineKeyboardButton(text="📣 Рассылка всем", callback_data="admin:broadcast"))
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Кнопка возврата в главное меню."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены FSM-сценария."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✕ Отменить", callback_data="admin:cancel"))
    return builder.as_markup()


__all__ = [
    "main_menu_kb",
    "pro_plans_kb",
    "alerts_list_kb",
    "admin_panel_kb",
    "back_to_menu_kb",
    "cancel_kb",
]
