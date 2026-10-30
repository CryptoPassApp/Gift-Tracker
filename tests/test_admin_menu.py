"""Кнопка «🛠 Админка» в главном меню: видна только админам, с возвратом в меню."""
from __future__ import annotations

from bot.keyboards import admin_panel_kb, main_menu_kb


def _texts(kb) -> list[str]:
    return [btn.text for row in kb.inline_keyboard for btn in row]


def test_admin_button_hidden_by_default():
    kb = main_menu_kb("https://app.example.com")
    assert "🛠 Админка" not in _texts(kb)


def test_admin_button_shown_only_with_flag():
    kb = main_menu_kb("https://app.example.com", is_admin=True)
    assert "🛠 Админка" in _texts(kb)
    panels = [
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == "admin:panel"
    ]
    assert len(panels) == 1


def test_admin_button_absent_without_mini_app_url():
    kb = main_menu_kb(None, is_admin=True)
    texts = _texts(kb)
    assert "🛠 Админка" in texts
    assert "⚡ Открыть Sniper Terminal" not in texts


def test_admin_panel_has_back_to_menu():
    kb = admin_panel_kb()
    assert any(
        btn.callback_data == "menu:start" for row in kb.inline_keyboard for btn in row
    )
