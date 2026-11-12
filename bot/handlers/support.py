"""Тех-поддержка внутри бота: диалог юзера с админом без внешних ссылок.

Юзер жмет «Написать в поддержку», пишет сообщение — оно сохраняется в
тикет в БД. В личный чат админа ничего не шлется: все обращения админ
читает в админке, раздел «Сообщения от людей» — кнопки-диалоги
(#id · ник, 5 на страницу со стрелками), история, ответ конкретному
человеку (FSM), завершение диалога.

FSM-перехватчики пропускают команды (``~F.text.startswith("/")``),
поэтому /start, /grant и т.д. работают даже в режиме чата.
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.handlers.ui import edit_screen
from bot.keyboards import (
    admin_panel_kb,
    admin_reply_kb,
    admin_support_dialog_kb,
    back_to_menu_kb,
    support_kb,
    support_open_kb,
    support_write_kb,
)
from bot.models import SupportMessage, SupportTicket, User

logger = logging.getLogger(__name__)

router = Router(name="support")

# Диалогов на одной странице экрана «Сообщения от людей».
SUP_PAGE_SIZE = 5
# Сообщений истории на экране диалога.
SUP_HISTORY_LIMIT = 6
# Лимит текста одного сообщения поддержки (запас под заголовок Telegram).
SUP_TEXT_LIMIT = 3900

MEDIA_KINDS = (
    ("photo", "фото"),
    ("video", "видео"),
    ("voice", "голосовое"),
    ("audio", "аудио"),
    ("document", "файл"),
    ("sticker", "стикер"),
    ("animation", "GIF"),
    ("video_note", "видеокружение"),
)


class SupportFSM(StatesGroup):
    """Состояния сценария «юзер пишет в поддержку»."""

    waiting_message = State()


class SupportAdminFSM(StatesGroup):
    """Состояние «админ отвечает конкретному пользователю»."""

    waiting_reply = State()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS


def _clip(text: str, limit: int = SUP_TEXT_LIMIT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _media_kind(message: Message) -> str | None:
    for attr, label in MEDIA_KINDS:
        if getattr(message, attr, None) is not None:
            return label
    return None


def _content_text(message: Message) -> str | None:
    """Текст сообщения поддержки: текст или подпись к медиа."""
    return message.text or message.caption


def _who(db_user: User) -> str:
    """Ник для заголовков админских сообщений: @username или имя."""
    if db_user.username:
        return f"@{db_user.username}"
    return f"«{db_user.first_name or 'без имени'}»"


def _full_who(db_user: User) -> str:
    """Ник + telegram_id — админ сразу видит, кому отвечает."""
    return f"{_who(db_user)} · id {db_user.telegram_id}"


def _ts(dt: datetime | None) -> str:
    return dt.strftime("%d.%m %H:%M") if dt else "—"


# ----------------------------------------------------------------------
# Работа с тикетами
# ----------------------------------------------------------------------
async def _open_ticket(session: AsyncSession, user_id: int) -> SupportTicket | None:
    return (
        await session.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id, SupportTicket.status == "open")
            .order_by(SupportTicket.id.desc())
        )
    ).scalars().first()


async def _open_dialog_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count(SupportTicket.id)).where(SupportTicket.status == "open")
            )
        ).scalar_one()
    )


async def _ticket_with_user(
    session: AsyncSession, ticket_id: int
) -> tuple[SupportTicket, User] | None:
    row = (
        await session.execute(
            select(SupportTicket, User)
            .join(User, User.id == SupportTicket.user_id)
            .where(SupportTicket.id == ticket_id)
        )
    ).first()
    return row


# ----------------------------------------------------------------------
# Юзерская сторона: экран, режим чата, завершение
# ----------------------------------------------------------------------
SUPPORT_TEXT = (
    "🛠 <b>Тех-поддержка</b>\n"
    "\n"
    "Вопрос или проблема? Пиши прямо в этот чат — никаких сторонних ботов.\n"
    "\n"
    "💬 Жми «Написать в поддержку» и печатай сообщение: оно уйдет "
    "разработчику, а его ответ придет тебе сюда же.\n"
    "\n"
    "Частые темы:\n"
    "• Не приходит пуш / пропала сделка\n"
    "• Оплата Stars или криптой\n"
    "• Реферальные начисления"
)


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Экран «Тех-поддержка»: чат с саппортом прямо в боте."""
    ticket = await _open_ticket(session, db_user.id)
    await edit_screen(callback, SUPPORT_TEXT, support_kb(has_open_ticket=ticket is not None))
    await callback.answer()


@router.callback_query(F.data == "support:write")
async def cb_support_write(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    """Вход в режим чата с поддержкой: следующие сообщения уходят админу."""
    await state.set_state(SupportFSM.waiting_message)
    if callback.message is not None:
        await callback.message.answer(
            "✍️ <b>Пиши прямо в чат</b> — сообщение уйдет в тех-поддержку.\n"
            "Можно текст, фото или файл. Ответ придет сюда же.",
            reply_markup=support_write_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "support:cancel")
async def cb_support_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Юзер передумал писать — выходим из режима ввода, диалог не трогаем."""
    await state.clear()
    if callback.message is not None:
        await callback.message.answer("Хорошо, режим ввода закрыт.")
    await callback.answer()


@router.callback_query(F.data == "support:close")
async def cb_support_close(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User
) -> None:
    """Юзер завершает диалог (или выходит из режима чата)."""
    await state.clear()
    ticket = await _open_ticket(session, db_user.id)
    if ticket is None:
        await callback.answer("Открытого диалога нет", show_alert=True)
        return
    ticket.status = "closed"
    ticket.updated_at = datetime.utcnow()
    await callback.message.answer(
        "✕ <b>Диалог завершен</b>\n"
        "Если что-то понадобится — «Тех-поддержка» → «Написать в поддержку».",
        reply_markup=back_to_menu_kb(),
    )
    await callback.answer()
    logger.info("Support ticket #%s closed by user %s", ticket.id, db_user.telegram_id)


@router.message(SupportFSM.waiting_message, ~F.text.startswith("/"))
async def process_user_support(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Сообщение юзера в режиме чата: сохранить в тикет.

    В личный чат админа ничего не шлется — обращения читаются в админке
    («Сообщения от людей», у экрана списка на кнопках счетчик новых).
    """
    ticket = await _open_ticket(session, db_user.id)
    if ticket is None:
        ticket = SupportTicket(user_id=db_user.id)
        session.add(ticket)
        await session.flush()

    raw_text = _content_text(message) or f"(медиа: {_media_kind(message)})"
    session.add(
        SupportMessage(ticket_id=ticket.id, from_admin=False, text=_clip(raw_text))
    )
    ticket.updated_at = datetime.utcnow()
    await session.flush()

    await message.answer(
        "📨 <b>Отправлено в поддержку</b>\n"
        "Ответ придет прямо в этот чат. Пока диалог открыт — пиши дальше, "
        "все сообщения уйдут в тот же тикет.",
        reply_markup=support_open_kb(),
    )
    logger.info(
        "Support ticket #%s: user %s sent message", ticket.id, db_user.telegram_id
    )


# ----------------------------------------------------------------------
# Админская сторона: список диалогов, история, ответ, завершение
# ----------------------------------------------------------------------
async def _unread_by_ticket(session: AsyncSession, ticket_ids: list[int]) -> dict[int, int]:
    """Сколько сообщений юзера пришло после последнего ответа админа.

    Ответов админа в тикете не было — считаются все сообщения юзера.
    Считается по всем сообщениям тикетов (их немного — это чат поддержки).
    """
    if not ticket_ids:
        return {}
    rows = (
        await session.execute(
            select(SupportMessage.ticket_id, SupportMessage.from_admin)
            .where(SupportMessage.ticket_id.in_(ticket_ids))
            .order_by(SupportMessage.id)
        )
    ).all()
    unread: dict[int, int] = {}
    for ticket_id, from_admin in rows:
        if from_admin:
            unread[ticket_id] = 0
        else:
            unread[ticket_id] = unread.get(ticket_id, 0) + 1
    return unread


def _dialog_label(ticket: SupportTicket, user: User, unread: int) -> str:
    """Кнопка-диалог в списке: #id · ник · id — и «+N» при новых сообщениях."""
    label = f"#{ticket.id} · {_who(user)} · id {user.telegram_id}"
    if unread:
        label += f" · +{unread}"
    else:
        label += f" · {_ts(ticket.updated_at)}"
    return label


async def _render_admin_support(callback: CallbackQuery, session: AsyncSession, page: int) -> None:
    """Экран «Сообщения от людей»: список открытых диалогов."""
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    total = await _open_dialog_count(session)
    rows = (
        await session.execute(
            select(SupportTicket, User)
            .join(User, User.id == SupportTicket.user_id)
            .where(SupportTicket.status == "open")
            .order_by(SupportTicket.updated_at.desc())
        )
    ).all()

    builder = InlineKeyboardBuilder()
    if not rows:
        text = (
            "💬 <b>Сообщения от людей</b>\n"
            "\n"
            "📭 Открытых диалогов нет.\n"
            "Здесь появятся обращения из раздела «Тех-поддержка»."
        )
        builder.row(InlineKeyboardButton(text="« В админку", callback_data="admin:panel"))
        await edit_screen(callback, text, builder.as_markup())
        await callback.answer()
        return

    pages = max(1, (len(rows) + SUP_PAGE_SIZE - 1) // SUP_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    window = rows[page * SUP_PAGE_SIZE:(page + 1) * SUP_PAGE_SIZE]
    unread = await _unread_by_ticket(session, [ticket.id for ticket, _ in window])

    for ticket, user in window:
        builder.row(
            InlineKeyboardButton(
                text=_dialog_label(ticket, user, unread.get(ticket.id, 0)),
                callback_data=f"supadm:view:{ticket.id}",
            )
        )
    if pages > 1:
        builder.row(
            InlineKeyboardButton(text="◀", callback_data=f"supadm:list:{(page - 1) % pages}"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton(text="▶", callback_data=f"supadm:list:{(page + 1) % pages}"),
        )
    builder.row(InlineKeyboardButton(text="« В админку", callback_data="admin:panel"))

    new_total = sum(
        unread.get(ticket.id, 0) for ticket, _ in window
    )
    text = (
        f"💬 <b>Сообщения от людей</b>\n"
        f"Открытых диалогов: <b>{total}</b>"
        + (f" · новых сообщений: <b>{new_total}</b>" if new_total else "")
        + "\n\n"
        "Нажми на диалог — история и ответ конкретному человеку."
    )
    await edit_screen(callback, text, builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "admin:support")
async def cb_admin_support(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    """Кнопка «Сообщения от людей» в админке."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    await _render_admin_support(callback, session, page=0)


@router.callback_query(F.data.startswith("supadm:list:"))
async def cb_admin_support_page(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """Пагинация списка диалогов."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        page = 0
    await _render_admin_support(callback, session, page=page)


@router.callback_query(F.data.startswith("supadm:view:"))
async def cb_admin_view_ticket(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    """История диалога + кнопки ответа и завершения."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    try:
        ticket_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Диалог не найден", show_alert=True)
        return

    row = await _ticket_with_user(session, ticket_id)
    if row is None:
        await callback.answer("Диалог не найден", show_alert=True)
        return
    ticket, user = row

    messages = list(
        reversed(
            list(
                (
                    await session.execute(
                        select(SupportMessage)
                        .where(SupportMessage.ticket_id == ticket.id)
                        .order_by(SupportMessage.id.desc())
                        .limit(SUP_HISTORY_LIMIT)
                    )
                ).scalars()
            )
        )
    )
    history = "\n\n".join(
        f"{_ts(m.created_at)} {'🛠 Поддержка:' if m.from_admin else f'👤 {_who(user)}:'}\n{_clip(m.text, 500)}"
        for m in messages
    ) or "— пока сообщений нет —"

    status = "🟢 открыт" if ticket.status == "open" else "⚪️ закрыт"
    text = (
        f"🎫 <b>Диалог #{ticket.id}</b> — {_full_who(user)}\n"
        f"{status} · обновлен {_ts(ticket.updated_at)}\n"
        "\n"
        f"<b>Переписка (последние {SUP_HISTORY_LIMIT}):</b>\n"
        f"{history}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_support_dialog_kb(ticket.id, is_open=ticket.status == "open"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("supadm:reply:"))
async def cb_admin_reply_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User
) -> None:
    """Админ жмет «Ответить»: следующие его сообщения уйдут этому юзеру."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    try:
        ticket_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Диалог не найден", show_alert=True)
        return

    row = await _ticket_with_user(session, ticket_id)
    if row is None:
        await callback.answer("Диалог не найден", show_alert=True)
        return
    ticket, user = row
    if ticket.status != "open":
        await callback.answer("Диалог уже закрыт", show_alert=True)
        return

    await state.set_state(SupportAdminFSM.waiting_reply)
    await state.update_data(ticket_id=ticket.id, user_tg_id=user.telegram_id)
    if callback.message is not None:
        await callback.message.answer(
            f"✍️ <b>Ответ для {_who(user)}</b> (диалог #{ticket.id})\n"
            "Напиши сообщение — юзер получит его от имени поддержки "
            "прямо в чате бота.",
            reply_markup=admin_reply_kb(ticket.id),
        )
    await callback.answer()


@router.callback_query(F.data == "supadm:cancel")
async def cb_admin_reply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Админ передумал отвечать."""
    await state.clear()
    if callback.message is not None:
        await callback.message.answer("Ответ отменен.")
    await callback.answer()


@router.message(SupportAdminFSM.waiting_reply, ~F.text.startswith("/"))
async def process_admin_reply(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    bot: Bot,
) -> None:
    """Ответ админа: доставить юзеру и записать в историю диалога."""
    if not _is_admin(db_user.telegram_id):
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    user_tg_id = data.get("user_tg_id")
    if ticket_id is None or user_tg_id is None or message.bot is None:
        await state.clear()
        await message.answer("⚠️ Диалог не найден, режим ответа сброшен.")
        return

    row = await _ticket_with_user(session, int(ticket_id))
    if row is None:
        await state.clear()
        await message.answer("⚠️ Диалог не найден, режим ответа сброшен.")
        return
    ticket, user = row
    if ticket.status != "open":
        await state.clear()
        await message.answer(
            f"⚠️ Диалог #{ticket.id} уже закрыт — ответ не ушел. "
            "Юзер может открыть новый через «Тех-поддержка»."
        )
        return

    raw_text = _content_text(message) or f"(медиа: {_media_kind(message)})"
    session.add(
        SupportMessage(ticket_id=ticket.id, from_admin=True, text=_clip(raw_text))
    )
    ticket.updated_at = datetime.utcnow()
    await session.flush()

    delivered = await _deliver_reply(message.bot, user.telegram_id, message)
    if delivered:
        await message.answer(
            f"✅ Доставлено {_full_who(user)} (диалог #{ticket.id}).\n"
            "Можно отправить еще сообщение или завершить диалог.",
            reply_markup=admin_reply_kb(ticket.id),
        )
        logger.info(
            "Support ticket #%s: admin replied to %s", ticket.id, user.telegram_id
        )
    else:
        await message.answer(
            f"⚠️ Не доставлено: {_full_who(user)} — вероятно, юзер заблокировал "
            "бота. Сообщение сохранено в истории диалога.",
        )
        logger.warning(
            "Support ticket #%s: reply to %s not delivered", ticket.id, user.telegram_id
        )


async def _deliver_reply(bot: Bot, user_tg_id: int, message: Message) -> bool:
    """Шлет ответ юзеру (текст с заголовком, медиа — копией с подписью)."""
    header = "💬 <b>Ответ тех-поддержки</b>\n\n"
    kb = support_open_kb()
    try:
        if message.text is not None:
            try:
                await bot.send_message(
                    user_tg_id, header + message.text.strip(), reply_markup=kb
                )
            except TelegramBadRequest:
                # Админ использовал «сырой» HTML типа «<3» — шлем без разметки.
                await bot.send_message(
                    user_tg_id,
                    header + message.text.strip(),
                    parse_mode=None,
                    reply_markup=kb,
                )
        else:
            caption = None
            if message.caption:
                caption = ("💬 Ответ тех-поддержки\n\n" + message.caption)[:1024]
            await bot.copy_message(
                user_tg_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=caption,
                reply_markup=kb,
            )
        return True
    except Exception:  # noqa: BLE001 — юзер мог заблокировать бота
        logger.warning("Support reply delivery failed to %s", user_tg_id, exc_info=True)
        return False


@router.callback_query(F.data.startswith("supadm:close:"))
async def cb_admin_close_ticket(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Админ завершает диалог: статус closed + уведомление юзеру."""
    if not _is_admin(db_user.telegram_id):
        await callback.answer("⛔ Только для админов", show_alert=True)
        return
    try:
        ticket_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Диалог не найден", show_alert=True)
        return

    row = await _ticket_with_user(session, ticket_id)
    if row is None:
        await callback.answer("Диалог не найден", show_alert=True)
        return
    ticket, user = row

    # Если админ был в режиме ответа именно этому юзеру — выходим из него.
    fsm_data = await state.get_data()
    if fsm_data.get("ticket_id") == ticket.id:
        await state.clear()

    if ticket.status != "open":
        await callback.answer("Диалог уже закрыт", show_alert=True)
        return

    ticket.status = "closed"
    ticket.updated_at = datetime.utcnow()
    await callback.answer(f"Диалог #{ticket.id} завершен")
    if callback.message is not None:
        await callback.message.answer(f"✅ Диалог #{ticket.id} завершен.")
    try:
        await callback.bot.send_message(
            user.telegram_id,
            "✅ <b>Тех-поддержка завершила диалог</b>\n"
            "Если что-то понадобится — раздел «Тех-поддержка» → "
            "«Написать в поддержку».",
            reply_markup=back_to_menu_kb(),
        )
    except Exception:  # noqa: BLE001 — юзер мог заблокировать бота
        logger.warning(
            "Support close notify failed for %s", user.telegram_id, exc_info=True
        )
    logger.info("Support ticket #%s closed by admin", ticket.id)


# Бейдж открытых диалогов на панели — используется хендлерами admin.py.
async def admin_panel_with_badge(session: AsyncSession, **panel_kwargs) -> InlineKeyboardMarkup:
    """Админ-панель со счетчиком открытых диалогов поддержки."""
    return admin_panel_kb(open_dialogs=await _open_dialog_count(session), **panel_kwargs)


__all__ = ["router", "SupportFSM", "SupportAdminFSM", "admin_panel_with_badge"]
