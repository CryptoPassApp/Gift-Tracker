"""Тесты админ-панели: активные PRO-подписчики и клавиатура без дублей."""
from __future__ import annotations

from bot.keyboards import admin_panel_kb
from bot.models import User
from bot.services.admin_stats import active_pro_users, grant_pro, revoke_pro


async def test_active_pro_users_filters_expired(db):
    async with db.session() as session:
        await grant_pro(session, 9301, "month", month_days=30)   # активный
        await grant_pro(session, 9302, "lifetime")               # навсегда
        user = await grant_pro(session, 9303, "month", month_days=30)
        await revoke_pro(session, 9303)                          # отозван
        session.add(User(telegram_id=9304))                      # FREE — не PRO
        await session.flush()

        actives = {u.telegram_id for u in await active_pro_users(session)}
    assert actives == {9301, 9302}


async def test_active_pro_users_orders_lifetime_first(db):
    async with db.session() as session:
        await grant_pro(session, 9311, "month", month_days=30)
        await grant_pro(session, 9312, "lifetime")
        users = [u.telegram_id for u in await active_pro_users(session)]
    assert users[0] == 9312  # навсегда — первым


def test_admin_panel_kb_stats_flag():
    kb = admin_panel_kb(include_stats=False)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "admin:stats" not in callbacks
    assert "admin:pro_list" in callbacks


def test_admin_panel_kb_pro_list_flag():
    kb = admin_panel_kb(include_pro_list=False)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "admin:pro_list" not in callbacks
    assert "admin:stats" in callbacks


def test_admin_panel_kb_full_has_both():
    kb = admin_panel_kb()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "admin:stats" in callbacks and "admin:pro_list" in callbacks
