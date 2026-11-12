"""Тесты бана: сервис ban_user/unban_user, отсечение пушей забаненных,
403 на Mini App API для забаненного пользователя, команда /ban /unban."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from bot.database import get_or_create_user
from bot.handlers import admin as admin_handler
from bot.models import User
from bot.services.admin_stats import ban_user, grant_pro_days, unban_user
from tests.conftest import TEST_BOT_TOKEN, make_settings, sign_init_data
from tests.test_payments_admin import FakeMessage


async def _mk_user(session, telegram_id: int) -> None:
    """Бан работает только по существующим юзерам — создаем вручную."""
    session.add(User(telegram_id=telegram_id))
    await session.flush()


async def test_ban_user_sets_fields(db):
    async with db.session() as session:
        await _mk_user(session, 9201)
        user, was_banned = await ban_user(session, 9201, "Спам и скам")
        assert user.telegram_id == 9201
        assert was_banned is False
        assert user.is_banned is True
        assert user.ban_reason == "Спам и скам"


async def test_ban_reports_repeat_ban_and_updates_reason(db):
    async with db.session() as session:
        await _mk_user(session, 9202)
        await ban_user(session, 9202, "спам")
        user, was_banned = await ban_user(session, 9202, "повторный спам")
        assert was_banned is True
        assert user.ban_reason == "повторный спам"


async def test_ban_default_reason_and_empty_reason(db):
    async with db.session() as session:
        await _mk_user(session, 9203)
        user, _ = await ban_user(session, 9203, "   ")
        assert user.ban_reason == "нарушение правил"


async def test_unban_clears_ban(db):
    async with db.session() as session:
        await _mk_user(session, 9204)
        await ban_user(session, 9204, "спам")
        user, previous = await unban_user(session, 9204)
        assert user.is_banned is False and user.ban_reason is None
        assert previous == "спам"


async def test_unban_not_banned_raises(db):
    async with db.session() as session:
        await _mk_user(session, 9205)
        with pytest.raises(ValueError):
            await unban_user(session, 9205)


async def test_ban_unknown_user_raises(db):
    async with db.session() as session:
        with pytest.raises(ValueError):
            await ban_user(session, 999999, "x")


def test_notifier_skips_banned_users(db):
    """Забаненные не попадают в рассылку сигналов."""
    from bot.config import get_settings
    from bot.services.notifier import Notifier

    recipients: list[int] = []
    notifier = Notifier.__new__(Notifier)

    async def fake_deliver(user, caption, keyboard, photo_url, delay):
        recipients.append(user.telegram_id)

    notifier._deliver = fake_deliver
    notifier._tasks = set()
    notifier._semaphore = asyncio.Semaphore(10)
    notifier.settings = get_settings()

    ok = User(telegram_id=1, is_banned=False)
    banned = User(telegram_id=2, is_banned=True, ban_reason="x")

    class _Signal:
        gift_name = "X"
        lot_number = "#1"
        floor_price = 10.0
        deal_price = 5.0
        discount_pct = 40.0
        net_profit_ton = 3.0
        marketplace_url = "https://getgems.io/nft/x"
        image_url = None
        item_address = None
        source = "getgems"
        is_hot = True
        model = None
        backdrop = None
        symbol = None
        floor_basis = None
        is_auction = False
        auction_end_ts = None

    asyncio.run(notifier.push_signal(_Signal(), [ok, banned]))
    assert recipients == [1]


def test_api_403_for_banned_user(client):
    """Забаненный пользователь получает 403 с причиной на весь Mini App API."""
    db = client.app.state.db

    async def _insert():
        async with db.session() as session:
            session.add(
                User(telegram_id=777001, is_banned=True, ban_reason="скам-поведение")
            )

    asyncio.run(_insert())
    init_data = sign_init_data(TEST_BOT_TOKEN, {"id": 777001, "first_name": "Banned"})
    r = client.get("/api/user/me", headers={"X-Init-Data": init_data})
    assert r.status_code == 403
    assert "скам-поведение" in r.text


def test_api_allows_not_banned_user(client):
    db = client.app.state.db

    async def _insert():
        async with db.session() as session:
            session.add(User(telegram_id=777002))

    asyncio.run(_insert())
    init_data = sign_init_data(TEST_BOT_TOKEN, {"id": 777002, "first_name": "Ok"})
    r = client.get("/api/user/me", headers={"X-Init-Data": init_data})
    assert r.status_code == 200


# ----------------------------------------------------------------------
# Регрессии команды /ban /unban (бот-хендлеры)
# ----------------------------------------------------------------------
async def _admin(db, tmp_path, monkeypatch, telegram_id: int = 111):
    """Админ-окружение: settings с ADMIN_IDS + admin-юзер в БД."""
    settings = make_settings(tmp_path, ADMIN_IDS=[telegram_id])
    monkeypatch.setattr(admin_handler, "get_settings", lambda: settings)
    async with db.session() as session:
        admin_user = await get_or_create_user(session, {"id": telegram_id})
    return admin_user


async def _mk_target(db, telegram_id: int, username: str | None = None) -> None:
    """Бан работает только по существующим юзерам — создаем жертву."""
    async with db.session() as session:
        payload = {"id": telegram_id}
        if username:
            payload["username"] = username
        await get_or_create_user(session, payload)


async def test_ban_command_persists_ban_and_reason(db, tmp_path, monkeypatch):
    """/ban обязан сохранять бан даже с причиной, ломающей HTML-разметку.

    Прод-регрессия: ответ админу содержал литерал «/unban <id>» — Telegram
    валидировал <id> как тег, TelegramBadRequest откатывал сессию БД
    вместе с уже примененным баном (в БД оставалось 0 забаненных).
    """
    admin_user = await _admin(db, tmp_path, monkeypatch)
    await _mk_target(db, 9301)
    message = FakeMessage(text="/ban 9301 скам <a href='x'>попытка</a>")
    async with db.session() as session:
        await admin_handler.cmd_ban(message, session, admin_user)

    assert "забанен" in message.answers[-1]
    # Причина не рвет разметку: угловые скобки экранированы.
    assert "<a href" not in message.answers[-1]
    assert "&lt;a href" in message.answers[-1]
    # В ответе нет «голого» тега <id> (им он ломался в проде).
    assert "<id>" not in message.answers[-1]
    async with db.session() as session:
        stored = (
            await session.execute(select(User).where(User.telegram_id == 9301))
        ).scalar_one()
    assert stored.is_banned is True
    assert "скам" in stored.ban_reason


async def test_ban_command_repeat_updates_reason(db, tmp_path, monkeypatch):
    """Повторный /ban обновляет причину, а не падает."""
    admin_user = await _admin(db, tmp_path, monkeypatch)
    await _mk_target(db, 9302)
    async with db.session() as session:
        await admin_handler.cmd_ban(FakeMessage(text="/ban 9302 спам"), session, admin_user)
    message = FakeMessage(text="/ban 9302 повторный скам")
    async with db.session() as session:
        await admin_handler.cmd_ban(message, session, admin_user)

    assert "забанен" in message.answers[-1]
    assert "повторный скам" in message.answers[-1]
    async with db.session() as session:
        stored = (
            await session.execute(select(User).where(User.telegram_id == 9302))
        ).scalar_one()
    assert stored.is_banned is True
    assert stored.ban_reason == "повторный скам"


async def test_unban_command_after_ban_restores_access(db, tmp_path, monkeypatch):
    """Связка /ban → /unban: бан снят, причина показана админу."""
    admin_user = await _admin(db, tmp_path, monkeypatch)
    await _mk_target(db, 9303)
    async with db.session() as session:
        await admin_handler.cmd_ban(FakeMessage(text="/ban 9303 скам"), session, admin_user)

    message = FakeMessage(text="/unban 9303")
    async with db.session() as session:
        await admin_handler.cmd_unban(message, session, admin_user)

    assert "Бан снят" in message.answers[-1]
    assert "скам" in message.answers[-1]
    async with db.session() as session:
        stored = (
            await session.execute(select(User).where(User.telegram_id == 9303))
        ).scalar_one()
    assert stored.is_banned is False
    assert stored.ban_reason is None


async def test_unban_without_ban_shows_error(db, tmp_path, monkeypatch):
    admin_user = await _admin(db, tmp_path, monkeypatch)
    async with db.session() as session:
        await get_or_create_user(session, {"id": 9304})
    message = FakeMessage(text="/unban 9304")
    async with db.session() as session:
        await admin_handler.cmd_unban(message, session, admin_user)
    assert "Не получилось" in message.answers[-1]


# ----------------------------------------------------------------------
# Бан/разбан по @username: прод-кейс «/unban без числового ID»
# ----------------------------------------------------------------------
async def test_ban_and_unban_by_username(db, tmp_path, monkeypatch):
    """Полный цикл по юзернейму: /ban @u причина → /unban @u — оба работают."""
    admin_user = await _admin(db, tmp_path, monkeypatch)
    await _mk_target(db, 9305, username="banmeplz")

    ban_msg = FakeMessage(text="/ban @banmeplz спам по юзернейму")
    async with db.session() as session:
        await admin_handler.cmd_ban(ban_msg, session, admin_user)
    assert "забанен" in ban_msg.answers[-1]
    stored = await load_user_by_id(db, 9305)
    assert stored.is_banned is True

    unban_msg = FakeMessage(text="/unban @banmeplz")
    async with db.session() as session:
        await admin_handler.cmd_unban(unban_msg, session, admin_user)
    assert "Бан снят" in unban_msg.answers[-1]
    stored = await load_user_by_id(db, 9305)
    assert stored.is_banned is False and stored.ban_reason is None


async def test_unban_unknown_username_shows_clear_error(db, tmp_path, monkeypatch):
    """Юзернейма нет в базе → внятная ошибка, а не «успешная» справка."""
    admin_user = await _admin(db, tmp_path, monkeypatch)
    message = FakeMessage(text="/unban @never_wrote_to_bot")
    async with db.session() as session:
        await admin_handler.cmd_unban(message, session, admin_user)
    assert "Не нашел" in message.answers[-1]
    assert "ормат" in "".join(message.answers)


async def test_unban_without_args_shows_info_not_success(db, tmp_path, monkeypatch):
    """/unban без аргумента = справка с ℹ️, а не «успех» с ✅.

    Прод-путаница: старая подсказка начиналась с «✅ Разбан» и слов
    «возвращает доступ» — админ читал ее как подтверждение разбана.
    """
    admin_user = await _admin(db, tmp_path, monkeypatch)
    message = FakeMessage(text="/unban")
    async with db.session() as session:
        await admin_handler.cmd_unban(message, session, admin_user)
    assert "формат команды" in message.answers[-1]
    assert not message.answers[-1].startswith("✅")


# ----------------------------------------------------------------------
# Регрессия /grant с числовым сроком: UnboundLocalError терял выдачу PRO
# ----------------------------------------------------------------------
async def test_grant_command_numeric_days_persists_pro(db, tmp_path, monkeypatch):
    """/grant <id> <дней> обязан выдавать PRO и отвечать админу.

    Прод-регрессия: после успешного grant_pro_days хендлер пересчитывал
    plan_label от переменной normalized (не определена в числовой ветке)
    → UnboundLocalError → откат сессии, PRO не сохранялся.
    """
    admin_user = await _admin(db, tmp_path, monkeypatch)
    message = FakeMessage(text="/grant 9401 10")
    async with db.session() as session:
        await admin_handler.cmd_grant(message, session, admin_user)

    assert "PRO выдан" in message.answers[-1]
    assert "10 дней" in message.answers[-1]
    stored = await load_user_by_id(db, 9401)
    assert stored.is_pro is True


async def load_user_by_id(db, telegram_id: int) -> User:
    async with db.session() as session:
        return (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one()
