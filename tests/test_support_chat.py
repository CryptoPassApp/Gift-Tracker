"""Чат тех-поддержки внутри бота: юзер -> тикет -> админ -> ответ -> закрытие."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.database import get_or_create_user
from bot.handlers import support as support_handler
from bot.models import SupportMessage, SupportTicket, User
from tests.conftest import make_settings
from tests.test_monetization import FakeBot

ADMIN_TG_ID = 999_001
USER_TG_ID = 800_200


class RelayBot(FakeBot):
    """FakeBot, который запоминает и reply_markup отправленных сообщений."""

    def __init__(self):
        super().__init__()
        self.markups: list[object | None] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append((chat_id, text))
        self.markups.append(kwargs.get("reply_markup"))


class FakeMessage:
    """Мини-двойник aiogram.Message для сценария поддержки."""

    def __init__(
        self, text: str = "", chat_id: int = 1, caption: str | None = None, bot=None
    ) -> None:
        self.text = text
        self.caption = caption
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = 4242
        self.bot = bot
        self.answers: list[tuple[str, dict | None]] = []
        self.copies: list[dict] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs or None))

    async def _fake_copy(self, **kwargs) -> None:  # pragma: no cover — для медиа
        self.copies.append(kwargs)


def make_state(user_id: int, chat_id: int | None = None) -> FSMContext:
    """FSM-контекст с изолированным ключом (как в реальном диспетчере)."""
    storage = MemoryStorage()
    return FSMContext(
        storage=storage,
        key=StorageKey(
            bot_id=1,
            chat_id=chat_id if chat_id is not None else user_id,
            user_id=user_id,
        ),
    )


class FakeCallback:
    """CallbackQuery-двойник: message.edit_text/answer + bot + answer()."""

    def __init__(self, data: str, bot: RelayBot | None = None) -> None:
        self.data = data
        self.bot = bot
        self.answers: list[tuple[str | None, bool]] = []
        self.edits: list[tuple[str, object | None]] = []
        self.new_messages: list[tuple[str, object | None]] = []
        self.message = SimpleNamespace(edit_text=self._edit, answer=self._new)

    async def _edit(self, text: str, reply_markup=None, **kwargs) -> None:
        self.edits.append((text, reply_markup))

    async def _new(self, text: str, reply_markup=None, **kwargs) -> None:
        self.new_messages.append((text, reply_markup))

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs) -> None:
        self.answers.append((text, show_alert))


async def make_users(db) -> tuple[User, User]:
    async with db.session() as session:
        admin = await get_or_create_user(
            session, {"id": ADMIN_TG_ID, "username": "boss", "first_name": "Boss"}
        )
        user = await get_or_create_user(
            session, {"id": USER_TG_ID, "username": "vasya", "first_name": "Vasya"}
        )
        await session.flush()
        admin_id, user_id = admin.id, user.id
    async with db.session() as session:
        admin = await session.get(User, admin_id)
        user = await session.get(User, user_id)
        return admin, user


async def load_tickets(db) -> list[SupportTicket]:
    async with db.session() as session:
        return list(
            (await session.execute(select(SupportTicket).order_by(SupportTicket.id))).scalars()
        )


async def load_messages(db, ticket_id: int) -> list[SupportMessage]:
    async with db.session() as session:
        return list(
            (
                await session.execute(
                    select(SupportMessage)
                    .where(SupportMessage.ticket_id == ticket_id)
                    .order_by(SupportMessage.id)
                )
            ).scalars()
        )


@pytest.fixture()
def support_settings(tmp_path):
    return make_settings(tmp_path, ADMIN_IDS=str(ADMIN_TG_ID))


@pytest.fixture()
def patched_settings(support_settings, monkeypatch):
    monkeypatch.setattr(support_handler, "get_settings", lambda: support_settings)
    return support_settings


class TestUserFlow:
    async def test_message_creates_ticket_and_relays_to_admin(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        message = FakeMessage(text="Пуши не приходят!", chat_id=USER_TG_ID)
        state = make_state(USER_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                message, state, session, user, bot
            )

        tickets = await load_tickets(db)
        assert len(tickets) == 1
        assert tickets[0].status == "open"

        saved = await load_messages(db, tickets[0].id)
        assert len(saved) == 1
        assert saved[0].from_admin is False
        assert saved[0].text == "Пуши не приходят!"

        # Сообщение продублировано админу с заголовком и кнопками ответа.
        assert bot.sent and bot.sent[0][0] == ADMIN_TG_ID
        assert "Диалог #1" in bot.sent[0][1]
        assert "vasya" in bot.sent[0][1]
        callbacks = [
            b.callback_data for row in bot.markups[0].inline_keyboard for b in row
        ]
        assert "supadm:reply:1" in callbacks
        assert "supadm:close:1" in callbacks

        # Юзер получил подтверждение и кнопки продолжения диалога.
        confirm_kb = message.answers[-1][1]["reply_markup"]
        user_callbacks = [
            b.callback_data for row in confirm_kb.inline_keyboard for b in row
        ]
        assert "support:write" in user_callbacks
        assert "support:close" in user_callbacks

    async def test_second_message_appends_to_same_ticket(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = FakeBot()
        state = make_state(USER_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="первое", chat_id=USER_TG_ID), state, session, user, bot
            )
            await support_handler.process_user_support(
                FakeMessage(text="второе", chat_id=USER_TG_ID), state, session, user, bot
            )

        tickets = await load_tickets(db)
        assert len(tickets) == 1
        saved = await load_messages(db, tickets[0].id)
        assert [m.text for m in saved] == ["первое", "второе"]

    async def test_closed_ticket_reopens_as_new(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = FakeBot()
        state = make_state(USER_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="раз", chat_id=USER_TG_ID), state, session, user, bot
            )
            await support_handler.cb_support_close(
                FakeCallback("support:close"), state, session, user
            )
        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="два", chat_id=USER_TG_ID), state, session, user, bot
            )

        tickets = await load_tickets(db)
        assert [t.status for t in tickets] == ["closed", "open"]

    async def test_user_close_notifies_admin(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        state = make_state(USER_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="помогите", chat_id=USER_TG_ID), state, session, user, bot
            )
            bot.sent.clear()
            bot.markups.clear()
            await support_handler.cb_support_close(
                FakeCallback("support:close", bot=bot), state, session, user
            )

        tickets = await load_tickets(db)
        assert tickets[0].status == "closed"
        assert any("закрыл диалог" in text for _, text in bot.sent)


class TestAdminFlow:
    async def test_reply_delivered_and_saved(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        user_state = make_state(USER_TG_ID)
        admin_state = make_state(ADMIN_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="что с пушами?", chat_id=USER_TG_ID),
                user_state,
                session,
                user,
                bot,
            )
            # Админ жмет «Ответить» на пересланном сообщении.
            await support_handler.cb_admin_reply_start(
                FakeCallback("supadm:reply:1", bot=bot), admin_state, session, admin
            )
            assert await admin_state.get_state() == "SupportAdminFSM:waiting_reply"

            await support_handler.process_admin_reply(
                FakeMessage(text="Уже чиним. Ответим за час!", chat_id=ADMIN_TG_ID, bot=bot),
                admin_state,
                session,
                admin,
                bot,
            )

        tickets = await load_tickets(db)
        saved = await load_messages(db, tickets[0].id)
        assert saved[-1].from_admin is True
        assert "чиним" in saved[-1].text

        # Юзеру ушел ответ с заголовком и кнопками продолжения.
        to_user = next(text for chat, text in bot.sent if chat == USER_TG_ID)
        assert "Ответ тех-поддержки" in to_user

    async def test_reply_to_closed_ticket_blocked(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        user_state = make_state(USER_TG_ID)
        admin_state = make_state(ADMIN_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="ау", chat_id=USER_TG_ID), user_state, session, user, bot
            )
            await support_handler.cb_support_close(
                FakeCallback("support:close", bot=bot), user_state, session, user
            )
            await support_handler.cb_admin_reply_start(
                FakeCallback("supadm:reply:1", bot=bot), admin_state, session, admin
            )

        # Кнопка «Ответить» на закрытом диалоге не открывает режим ответа.
        assert await admin_state.get_state() is None
        reply = FakeMessage(text="опоздал с ответом", chat_id=ADMIN_TG_ID, bot=bot)
        async with db.session() as session:
            await support_handler.process_admin_reply(
                reply, admin_state, session, admin, bot
            )

        assert not any(chat == USER_TG_ID for chat, _ in bot.sent)
        assert any("не найден" in text for text, _ in reply.answers)

    async def test_admin_close_notifies_user(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        user_state = make_state(USER_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="привет", chat_id=USER_TG_ID), user_state, session, user, bot
            )

        bot.sent.clear()
        admin_state = make_state(ADMIN_TG_ID)
        async with db.session() as session:
            await support_handler.cb_admin_close_ticket(
                FakeCallback("supadm:close:1", bot=bot), admin_state, session, admin
            )

        tickets = await load_tickets(db)
        assert tickets[0].status == "closed"
        assert any("завершила диалог" in text for _, text in bot.sent)

    async def test_admin_close_is_idempotent(self, db, patched_settings):
        admin, user = await make_users(db)
        user_state = make_state(USER_TG_ID)
        admin_state = make_state(ADMIN_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="привет", chat_id=USER_TG_ID), user_state, session, user, FakeBot()
            )
        async with db.session() as session:
            await support_handler.cb_admin_close_ticket(
                FakeCallback("supadm:close:1"), admin_state, session, admin
            )
        callback = FakeCallback("supadm:close:1")
        async with db.session() as session:
            await support_handler.cb_admin_close_ticket(
                callback, admin_state, session, admin
            )
        assert callback.answers[-1][0] == "Диалог уже закрыт"


class TestAdminScreens:
    async def test_dialogs_screen_lists_open_tickets(self, db, patched_settings):
        admin, user = await make_users(db)
        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="вопрос про оплату", chat_id=USER_TG_ID),
                make_state(USER_TG_ID),
                session,
                user,
                FakeBot(),
            )

        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, admin)

        text, markup = callback.edits[-1]
        assert "Сообщения от людей" in text
        assert "Открытых диалогов: <b>1</b>" in text
        buttons = [b for row in markup.inline_keyboard for b in row]
        assert any("#1 · @vasya" in b.text for b in buttons)
        assert any(b.callback_data == "supadm:view:1" for b in buttons)

    async def test_dialogs_screen_empty(self, db, patched_settings):
        admin, _user = await make_users(db)
        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, admin)
        text, _markup = callback.edits[-1]
        assert "Открытых диалогов нет" in text

    async def test_view_shows_history_both_sides(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = FakeBot()
        user_state = make_state(USER_TG_ID)
        admin_state = make_state(ADMIN_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(
                FakeMessage(text="пропала сделка", chat_id=USER_TG_ID),
                user_state,
                session,
                user,
                bot,
            )
            await support_handler.cb_admin_reply_start(
                FakeCallback("supadm:reply:1", bot=bot), admin_state, session, admin
            )
            await support_handler.process_admin_reply(
                FakeMessage(text="проверяем ленту", chat_id=ADMIN_TG_ID, bot=bot),
                admin_state,
                session,
                admin,
                bot,
            )

        callback = FakeCallback("supadm:view:1")
        async with db.session() as session:
            await support_handler.cb_admin_view_ticket(callback, session, admin)

        text, markup = callback.edits[-1]
        assert "пропала сделка" in text
        assert "проверяем ленту" in text
        buttons = [b for row in markup.inline_keyboard for b in row]
        assert any(b.callback_data == "supadm:reply:1" for b in buttons)
        assert any(b.callback_data == "supadm:close:1" for b in buttons)

    async def test_panel_badge_counts_open_dialogs(self, db, patched_settings):
        admin, user = await make_users(db)
        async with db.session() as session:
            kb = await support_handler.admin_panel_with_badge(session)
            labels = [b.text for row in kb.inline_keyboard for b in row]
            assert all("Сообщения от людей" not in t or "·" not in t for t in labels)

            await support_handler.process_user_support(
                FakeMessage(text="снова я", chat_id=USER_TG_ID),
                make_state(USER_TG_ID),
                session,
                user,
                FakeBot(),
            )
            kb = await support_handler.admin_panel_with_badge(session)
            labels = [b.text for row in kb.inline_keyboard for b in row]
            assert any("Сообщения от людей · 1" in t for t in labels)

    async def test_non_admin_cannot_open_dialogs(self, db, patched_settings):
        _admin, user = await make_users(db)
        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, user)
        assert callback.edits == []
        assert callback.answers[0][0] == "⛔ Только для админов"
