"""Инлайн-клавиатуры бота: меню, тарифы PRO, алерты, админ-панель.

Навигация строится вокруг одного сообщения-экрана: кнопки редактируют
его (edit_text), а не шлют новые сообщения — иначе чат зарастает
дубликатами меню.
"""
from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.models import Alert

# Сколько алертов на одной странице экрана «Мои алерты».
ALERTS_PAGE_SIZE = 5


def main_menu_kb(mini_app_url: str | None, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню: запуск Mini App + разделы (+ админка для ADMIN_IDS)."""
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
    builder.row(
        InlineKeyboardButton(text="❓ Как это работает", callback_data="menu:how"),
    )
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="🛠 Админка", callback_data="admin:panel"),
        )
    return builder.as_markup()


def pro_plans_kb(crypto_prices: dict[str, float] | None = None) -> InlineKeyboardMarkup:
    """Выбор тарифа PRO: Stars всегда, крипто-кнопки при включенном Crypto Pay."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ PRO месяц — 150★", callback_data="pro:month"))
    builder.row(InlineKeyboardButton(text="👑 PRO навсегда — 999★", callback_data="pro:lifetime"))
    if crypto_prices:
        month = crypto_prices.get("pro_month")
        lifetime = crypto_prices.get("pro_lifetime")
        if month:
            builder.row(
                InlineKeyboardButton(text=f"💰 Месяц криптой — ${month:g}", callback_data="pro:crypto:month")
            )
        if lifetime:
            builder.row(
                InlineKeyboardButton(text=f"💰 Навсегда криптой — ${lifetime:g}", callback_data="pro:crypto:lifetime")
            )
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def crypto_invoice_kb(invoice_url: str, invoice_id: int) -> InlineKeyboardMarkup:
    """Оплата крипто-инвойса: ссылка в @CryptoBot + ручная проверка оплаты."""
    builder = InlineKeyboardBuilder()
    if invoice_url:
        builder.row(InlineKeyboardButton(text="💳 Оплатить в @CryptoBot", url=invoice_url))
    builder.row(
        InlineKeyboardButton(text="🔄 Я уже оплатил — обновить", callback_data=f"pro:crypto:check:{invoice_id}")
    )
    builder.row(InlineKeyboardButton(text="« К тарифам", callback_data="menu:pro"))
    return builder.as_markup()


def alerts_screen_kb(
    alerts: list[Alert],
    page: int = 0,
    mini_app_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Экран «Мои алерты»: постраничный список + создание + назад в меню.

    Каждая строка — алерт с кнопкой удаления (``alert_del:<id>:<page>``).
    При переполнении появляются ◀/▶ (замкнутая навигация по кругу).
    """
    builder = InlineKeyboardBuilder()
    total = len(alerts)
    pages = max(1, math.ceil(total / ALERTS_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    window = alerts[page * ALERTS_PAGE_SIZE:(page + 1) * ALERTS_PAGE_SIZE]

    for alert in window:
        builder.row(
            InlineKeyboardButton(
                text=f"✕ {alert.gift_name} ≤ {alert.max_price:g} TON (−{alert.min_discount:g}%)",
                callback_data=f"alert_del:{alert.id}:{page}",
            )
        )

    if pages > 1:
        builder.row(
            InlineKeyboardButton(text="◀", callback_data=f"alerts:page:{(page - 1) % pages}"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton(text="▶", callback_data=f"alerts:page:{(page + 1) % pages}"),
        )

    if mini_app_url and str(mini_app_url).startswith("https://"):
        builder.row(
            InlineKeyboardButton(
                text="➕ Создать алерт в терминале",
                web_app=WebAppInfo(url=mini_app_url),
            )
        )
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def admin_panel_kb() -> InlineKeyboardMarkup:
    """Админ-панель."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"))
    builder.row(InlineKeyboardButton(text="👤 Выдать PRO", callback_data="admin:grant"))
    builder.row(InlineKeyboardButton(text="📣 Рассылка всем", callback_data="admin:broadcast"))
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Кнопка возврата в главное меню."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


DISCOUNT_THRESHOLD_OPTIONS = (0, 15, 20, 25, 30, 40)


def settings_kb(current_min_discount: float) -> InlineKeyboardMarkup:
    """Порог дисконта для PRO-фильтра broadcast-сигналов."""
    builder = InlineKeyboardBuilder()
    buttons = []
    for value in DISCOUNT_THRESHOLD_OPTIONS:
        active = abs(float(current_min_discount) - value) < 0.01
        label = ("✓ " if active else "") + (f"от −{value}%" if value else "Выключен")
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"settings:disc:{value}")
        )
    builder.row(*buttons[:3])
    builder.row(*buttons[3:])
    builder.row(InlineKeyboardButton(text="« В меню", callback_data="menu:start"))
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены FSM-сценария."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✕ Отменить", callback_data="admin:cancel"))
    return builder.as_markup()


__all__ = [
    "ALERTS_PAGE_SIZE",
    "main_menu_kb",
    "pro_plans_kb",
    "crypto_invoice_kb",
    "alerts_screen_kb",
    "admin_panel_kb",
    "back_to_menu_kb",
    "cancel_kb",
    "settings_kb",
    "DISCOUNT_THRESHOLD_OPTIONS",
]
