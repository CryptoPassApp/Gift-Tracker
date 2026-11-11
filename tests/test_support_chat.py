"""Чат тех-поддержки внутри бота: тикеты в БД, чтение через админку.

В личный чат админа ничего не шлется: список людей — кнопками в админке
(5 на страницу + стрелки), у кнопки счетчик новых сообщений.
"""
from __future__ import annotations

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


async def make_user(db, tg_id: int, username: str) -> User:
    async with db.session() as session:
        user = await get_or_create_user(session, {"id": tg_id, "username": username})
        await session.flush()
        user_id = user.id
    async with db.session() as session:
        return await session.get(User, user_id)


async def write_ticket(db, user: User, text: str) -> None:
    """Юзер пишет сообщение в поддержку (режим чата)."""
    state = make_state(user.telegram_id)
    async with db.session() as session:
        await support_handler.process_user_support(
            FakeMessage(text=text, chat_id=user.telegram_id), state, session, user
        )


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


def _button_texts(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


@pytest.fixture()
def support_settings(tmp_path):
    return make_settings(tmp_path, ADMIN_IDS=str(ADMIN_TG_ID))


@pytest.fixture()
def patched_settings(support_settings, monkeypatch):
    monkeypatch.setattr(support_handler, "get_settings", lambda: support_settings)
    return support_settings


class TestUserFlow:
    async def test_message_creates_ticket_nothing_in_admin_chat(self, db, patched_settings):
        admin, user = await make_users(db)
        message = FakeMessage(text="Пуши не приходят!", chat_id=USER_TG_ID)
        state = make_state(USER_TG_ID)

        async with db.session() as session:
            await support_handler.process_user_support(message, state, session, user)

        tickets = await load_tickets(db)
        assert len(tickets) == 1
        assert tickets[0].status == "open"

        saved = await load_messages(db, tickets[0].id)
        assert len(saved) == 1
        assert saved[0].from_admin is False
        assert saved[0].text == "Пуши не приходят!"

        # Юзер получил подтверждение; в чат админа ничего не уходит.
        assert message.answers and "Отправлено в поддержку" in message.answers[-1][0]

    async def test_second_message_appends_to_same_ticket(self, db, patched_settings):
        _admin, user = await make_users(db)
        await write_ticket(db, user, "первое")
        await write_ticket(db, user, "второе")

        tickets = await load_tickets(db)
        assert len(tickets) == 1
        saved = await load_messages(db, tickets[0].id)
        assert [m.text for m in saved] == ["первое", "второе"]

    async def test_closed_ticket_reopens_as_new(self, db, patched_settings):
        _admin, user = await make_users(db)
        await write_ticket(db, user, "раз")

        state = make_state(USER_TG_ID)
        async with db.session() as session:
            await support_handler.cb_support_close(
                FakeCallback("support:close"), state, session, user
            )
        await write_ticket(db, user, "два")

        tickets = await load_tickets(db)
        assert [t.status for t in tickets] == ["closed", "open"]

    async def test_user_close_marks_ticket_closed(self, db, patched_settings):
        _admin, user = await make_users(db)
        await write_ticket(db, user, "помогите")

        state = make_state(USER_TG_ID)
        async with db.session() as session:
            await support_handler.cb_support_close(
                FakeCallback("support:close"), state, session, user
            )

        tickets = await load_tickets(db)
        assert tickets[0].status == "closed"


class TestAdminFlow:
    async def test_reply_delivered_and_saved(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        admin_state = make_state(ADMIN_TG_ID)

        await write_ticket(db, user, "что с пушами?")

        async with db.session() as session:
            # Админ жмет «Ответить» на экране диалога.
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

        saved = await load_messages(db, 1)
        assert saved[-1].from_admin is True
        assert "чиним" in saved[-1].text

        # Юзеру ушел ответ с заголовком.
        assert any(chat == USER_TG_ID for chat, _ in bot.sent)
        to_user = next(text for chat, text in bot.sent if chat == USER_TG_ID)
        assert "Ответ тех-поддержки" in to_user

    async def test_reply_to_closed_ticket_blocked(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        admin_state = make_state(ADMIN_TG_ID)

        await write_ticket(db, user, "ау")
        state = make_state(USER_TG_ID)
        async with db.session() as session:
            await support_handler.cb_support_close(
                FakeCallback("support:close"), state, session, user
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
        admin_state = make_state(ADMIN_TG_ID)

        await write_ticket(db, user, "привет")

        async with db.session() as session:
            await support_handler.cb_admin_close_ticket(
                FakeCallback("supadm:close:1", bot=bot), admin_state, session, admin
            )

        tickets = await load_tickets(db)
        assert tickets[0].status == "closed"
        assert any("завершила диалог" in text for _, text in bot.sent)

    async def test_admin_close_is_idempotent(self, db, patched_settings):
        admin, user = await make_users(db)
        admin_state = make_state(ADMIN_TG_ID)

        await write_ticket(db, user, "привет")

        async with db.session() as session:
            await support_handler.cb_admin_close_ticket(
                FakeCallback("supadm:close:1", bot=RelayBot()), admin_state, session, admin
            )
        callback = FakeCallback("supadm:close:1")
        async with db.session() as session:
            await support_handler.cb_admin_close_ticket(
                callback, admin_state, session, admin
            )
        assert callback.answers[-1][0] == "Диалог уже закрыт"


class TestAdminScreens:
    async def test_dialogs_screen_lists_tickets_with_unread(self, db, patched_settings):
        admin, user = await make_users(db)
        await write_ticket(db, user, "вопрос про оплату")
        await write_ticket(db, user, "и еще один вопрос")

        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, admin)

        text, markup = callback.edits[-1]
        assert "Сообщения от людей" in text
        assert "Открытых диалогов: <b>1</b>" in text
        assert "новых сообщений: <b>2</b>" in text
        labels = _button_texts(markup)
        # Кнопка: #id · ник · id юзера · +непрочитанные.
        assert any("#1 · @vasya · id 800200 · +2" in t for t in labels)
        assert any(b.callback_data == "supadm:view:1" for row in markup.inline_keyboard for b in row)

    async def test_unread_resets_after_admin_reply(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        admin_state = make_state(ADMIN_TG_ID)

        await write_ticket(db, user, "первый вопрос")
        await write_ticket(db, user, "второй")

        async with db.session() as session:
            await support_handler.cb_admin_reply_start(
                FakeCallback("supadm:reply:1", bot=bot), admin_state, session, admin
            )
            await support_handler.process_admin_reply(
                FakeMessage(text="ок, смотрю", chat_id=ADMIN_TG_ID, bot=bot),
                admin_state,
                session,
                admin,
                bot,
            )
        await write_ticket(db, user, "спасибо!")

        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, admin)

        text, markup = callback.edits[-1]
        assert "новых сообщений: <b>1</b>" in text
        assert any("#1 · @vasya · id 800200 · +1" in t for t in _button_texts(markup))

    async def test_dialogs_screen_empty(self, db, patched_settings):
        admin, _user = await make_users(db)
        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, admin)
        text, _markup = callback.edits[-1]
        assert "Открытых диалогов нет" in text

    async def test_pagination_five_per_page_with_arrows(self, db, patched_settings):
        admin, user = await make_users(db)
        await write_ticket(db, user, "текущий")
        for i in range(1, 8):  # 7 старых диалогов: всего 8
            extra = await make_user(db, 800_300 + i, f"user{i}")
            await write_ticket(db, extra, f"вопрос {i}")

        # Страница 0: 5 кнопок-диалогов + стрелки.
        callback = FakeCallback("admin:support")
        async with db.session() as session:
            await support_handler.cb_admin_support(callback, session, admin)
        text, markup = callback.edits[-1]
        assert "Открытых диалогов: <b>8</b>" in text
        dialogs = [t for t in _button_texts(markup) if t.startswith("#")]
        assert len(dialogs) == 5
        labels = _button_texts(markup)
        assert "◀" in labels and "▶" in labels and "1/2" in labels

        # Стрелка вперед: страница 2 — оставшиеся 3 диалога, назад можно.
        callback2 = FakeCallback("supadm:list:1")
        async with db.session() as session:
            await support_handler.cb_admin_support_page(callback2, session, admin)
        text2, markup2 = callback2.edits[-1]
        dialogs2 = [t for t in _button_texts(markup2) if t.startswith("#")]
        assert len(dialogs2) == 3
        assert "◀" in _button_texts(markup2) and "2/2" in _button_texts(markup2)

        # Стрелка назад со второй страницы: снова страница 1.
        callback3 = FakeCallback("supadm:list:0")
        async with db.session() as session:
            await support_handler.cb_admin_support_page(callback3, session, admin)
        dialogs3 = [
            t for t in _button_texts(callback3.edits[-1][1]) if t.startswith("#")
        ]
        assert len(dialogs3) == 5

    async def test_view_shows_history_both_sides(self, db, patched_settings):
        admin, user = await make_users(db)
        bot = RelayBot()
        admin_state = make_state(ADMIN_TG_ID)

        await write_ticket(db, user, "пропала сделка")
        async with db.session() as session:
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
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "supadm:reply:1" in callbacks
        assert "supadm:close:1" in callbacks
        assert "admin:support" in callbacks  # « К диалогам»

    async def test_panel_badge_counts_open_dialogs(self, db, patched_settings):
        admin, user = await make_users(db)
        async with db.session() as session:
            kb = await support_handler.admin_panel_with_badge(session)
            labels = [b.text for row in kb.inline_keyboard for b in row]
            assert all("Сообщения от людей" not in t or "·" not in t for t in labels)

        await write_ticket(db, user, "снова я")
        async with db.session() as session:
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
