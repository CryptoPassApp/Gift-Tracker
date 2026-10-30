"""Notifier — быстрая рассылка сигналов снайпера.

Правила монетизации (ЧАСТЬ 2 ТЗ):
  * PRO-пользователи получают пуши мгновенно (asyncio.gather-пачками);
  * FREE-пользователи — с задержкой FREE_PUSH_DELAY_SEC (45 секунд),
    чтобы самые жирные лоты перехватывали PRO-снайперы.

Формат пуша — как у маркет-парсеров: фото конкретного NFT, под ним
подпись с ценой/флором/профитом, ниже кнопки «Купить на маркете»,
«Подарок в Telegram» и терминал. Картинка отправляется по URL
(Telegram скачивает ее сам, upload не нужен); если URL не подошел —
сообщение уходит текстом с той же подписью и кнопками.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.config import Settings
from bot.models import Alert, User

if TYPE_CHECKING:  # pragma: no cover
    from bot.services.sniper_engine import Signal

logger = logging.getLogger(__name__)

# Человекочитаемые названия маркетплейсов для подписи и кнопки покупки.
MARKET_LABELS: dict[str, str] = {
    "getgems": "Getgems",
    "portals": "Portals",
    "tonnel": "Tonnel",
    "mrkt": "MRKT",
    "tgmrkt": "MRKT",
}

TELEGRAM_GIFT_URL_BASE = "https://t.me/nft/"
CAPTION_LIMIT = 1024


def market_label(source: str | None) -> str:
    return MARKET_LABELS.get((source or "").strip().lower(), "Getgems")


def telegram_gift_url(gift_name: str, lot_number: str | None) -> str | None:
    """Ссылка «Подарок в Telegram»: t.me/nft/ResistanceDog-7547.

    Слаг — имя коллекции без не-латинских символов («Durov's Cap» ->
    «DurovsCap», «Ion Gem» -> «IonGem»), номер — номер экземпляра из
    лота. Без номера ссылку не строим: t.me не прощает мусорных адресов.
    """
    match = re.search(r"(\d+)", lot_number or "")
    slug = re.sub(r"[^0-9A-Za-z]", "", gift_name or "")
    if not match or not slug:
        return None
    return f"{TELEGRAM_GIFT_URL_BASE}{slug}-{match.group(1)}"


def push_photo_url(image_url: str | None) -> str | None:
    """URL картинки для sendPhoto (None -> пуш уйдет текстом).

    Fragment хостит один и тот же рендер и в webp, и в JPEG; для sendPhoto
    берем .medium.jpg — JPEG Telegram принимает гарантированно. Portals
    сразу отдает фото лота, его оставляем как есть.
    """
    if not image_url or not image_url.startswith("http"):
        return None
    if "nft.fragment.com/gift/" in image_url and image_url.endswith(".webp"):
        return image_url[: -len(".webp")] + ".medium.jpg"
    return image_url


def _signal_title(signal: Signal) -> str:
    """«Resistance Dog #7547»; без номера в лоте — просто имя подарка."""
    if signal.lot_number and re.search(r"\d", signal.lot_number):
        return f"{signal.gift_name} {signal.lot_number}"
    return signal.gift_name


def _trait_lines(signal: Signal) -> str:
    """Атрибуты конкретного NFT: Модель / Фон / Узор."""
    lines = []
    if signal.model:
        lines.append(f"Модель: <b>{signal.model}</b>")
    if signal.backdrop:
        lines.append(f"Фон: <b>{signal.backdrop}</b>")
    if signal.symbol:
        lines.append(f"Узор: <b>{signal.symbol}</b>")
    return "\n".join(lines)


def _floor_line(signal: Signal) -> str:
    basis = signal.floor_basis or ""
    if basis == "mrkt_bid":
        return f"🛒 MRKT автобай: <b>{signal.floor_price:g} TON</b>"
    if basis.startswith("model:"):
        return f"📉 Флор модели {basis.split(':', 1)[1]}: <b>{signal.floor_price:g} TON</b>"
    return f"📉 Флор коллекции: <b>{signal.floor_price:g} TON</b>"


def _signal_caption(signal: Signal) -> str:
    """Подпись под фото лота (стиль маркет-парсеров)."""
    header = "🔥 HOT LIQUIDATION" if signal.is_hot else "⚡ Снайп-сигнал"
    parts = [
        f"{header} · <b>−{abs(signal.discount_pct):g}% от флора</b>",
        f"🎁 <b>{_signal_title(signal)}</b>",
    ]
    traits = _trait_lines(signal)
    if traits:
        parts.append(traits)
    parts += [
        "",
        f"💰 <b>{signal.deal_price:.2f} TON</b> · {market_label(signal.source)}",
        _floor_line(signal),
        f"💸 Профит после комиссий: <b>{signal.net_profit_ton:+.2f} TON</b>",
    ]
    return "\n".join(parts)


def _alert_caption(signal: Signal, alert: Alert) -> str:
    """Подпись пуша о сработавшем персональном алерте."""
    parts = [
        "🔔 <b>Ваш алерт сработал</b>",
        f"🎁 <b>{_signal_title(signal)}</b>",
    ]
    traits = _trait_lines(signal)
    if traits:
        parts.append(traits)
    parts += [
        "",
        f"💰 <b>{signal.deal_price:.2f} TON</b> · {market_label(signal.source)}",
        _floor_line(signal),
        f"💸 Профит: <b>{signal.net_profit_ton:+.2f} TON</b>",
        f"🎯 Триггер: ≤ {alert.max_price:g} TON · дисконт −{abs(signal.discount_pct):g}% "
        f"(порог −{alert.min_discount:g}%)",
    ]
    return "\n".join(parts)


def _snipe_keyboard(
    signal: Signal, mini_app_url: str | None = None
) -> InlineKeyboardMarkup:
    """Кнопки под фото: покупка на маркете, подарок в Telegram, терминал."""
    preposition = "в" if (signal.source or "").lower() == "portals" else "на"
    rows = [[
        InlineKeyboardButton(
            text=(
                f"🛒 Купить {preposition} {market_label(signal.source)} · "
                f"{signal.deal_price:.2f} TON"
            ),
            url=signal.marketplace_url,
        )
    ]]
    gift_url = telegram_gift_url(signal.gift_name, signal.lot_number)
    if gift_url:
        rows.append([InlineKeyboardButton(text="🎁 Подарок в Telegram", url=gift_url)])

    if mini_app_url and str(mini_app_url).startswith("https://"):
        rows.append([
            InlineKeyboardButton(
                text="📱 Открыть Sniper Terminal",
                web_app=WebAppInfo(url=mini_app_url),
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class Notifier:
    """Диспетчер пушей с задержками по тарифу пользователя."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bot: Bot | None = None
        self._semaphore = asyncio.Semaphore(settings.NOTIFIER_CONCURRENCY)
        # Ссылки на фоновые таски, чтобы их не съел сборщик мусора.
        self._tasks: set[asyncio.Task] = set()

    def bind(self, bot: Bot) -> None:
        """Подключает экземпляр бота (вызывается на старте polling)."""
        self.bot = bot

    # ------------------------------------------------------------------
    # Публичные методы рассылки
    # ------------------------------------------------------------------
    async def push_signal(self, signal: Signal, users: list[User]) -> None:
        """Рассылает горячий сигнал списку пользователей.

        PRO-пользователь может задать порог дисконта (User.min_discount):
        сигналы слабее порога ему не отправляются. Персональные алерты этот
        фильтр не затрагивают — у них собственный min_discount.
        """
        if not users:
            return
        caption = _signal_caption(signal)
        photo_url = push_photo_url(signal.image_url)
        keyboard = _snipe_keyboard(signal, mini_app_url=self.settings.MINI_APP_URL)
        queued = 0
        skipped = 0
        for user in users:
            is_pro = user.is_pro_active()
            threshold = float(user.min_discount or 0.0) if is_pro else 0.0
            if threshold > 0 and signal.discount_pct < threshold:
                skipped += 1
                continue
            delay = 0.0 if is_pro else float(self.settings.FREE_PUSH_DELAY_SEC)
            self._spawn(self._deliver(user, caption, keyboard, photo_url, delay))
            queued += 1
        logger.info(
            "Signal %s (-%s%%) queued to %d user(s), skipped by PRO filter: %d",
            signal.gift_name,
            signal.discount_pct,
            queued,
            skipped,
        )

    async def push_alert_matches(self, signal: Signal, alerts: list[Alert]) -> None:
        """Рассылает уведомления по персональным триггерам."""
        photo_url = push_photo_url(signal.image_url)
        for alert in alerts:
            user = alert.user
            if user is None:
                continue
            caption = _alert_caption(signal, alert)
            keyboard = _snipe_keyboard(signal, mini_app_url=self.settings.MINI_APP_URL)
            delay = 0.0 if user.is_pro_active() else float(self.settings.FREE_PUSH_DELAY_SEC)
            self._spawn(self._deliver(user, caption, keyboard, photo_url, delay))
        if alerts:
            logger.info("Alert matches for signal %s: %d", signal.gift_name, len(alerts))

    async def broadcast_admin_message(
        self, bot: Bot, from_chat_id: int, message_id: int, users: list[User]
    ) -> int:
        """Админ-рассылка: копирует сообщение всем пользователям (без задержек тарифа)."""
        sent = 0
        for recipient in users:
            try:
                async with self._semaphore:
                    await bot.copy_message(
                        chat_id=recipient.telegram_id,
                        from_chat_id=from_chat_id,
                        message_id=message_id,
                    )
                sent += 1
            except TelegramForbiddenError:
                logger.info("User %s blocked the bot — skipped", recipient.telegram_id)
            except TelegramAPIError as exc:
                logger.warning("Broadcast to %s failed: %s", recipient.telegram_id, exc)
            await asyncio.sleep(self.settings.BROADCAST_DELAY_SEC)
        return sent

    # ------------------------------------------------------------------
    # Внутренняя доставка
    # ------------------------------------------------------------------
    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(
        self,
        user: User,
        caption: str,
        keyboard: InlineKeyboardMarkup,
        photo_url: str | None,
        delay: float,
    ) -> None:
        """Ждет задержку тарифа и отправляет фото-пуш (ретрай + фолбэк на текст)."""
        if delay > 0:
            await asyncio.sleep(delay)
        if self.bot is None:
            logger.error("Notifier has no bot bound — message dropped")
            return

        caption = caption[:CAPTION_LIMIT]
        for attempt in range(3):
            try:
                async with self._semaphore:
                    if photo_url:
                        await self.bot.send_photo(
                            chat_id=user.telegram_id,
                            photo=photo_url,
                            caption=caption,
                            reply_markup=keyboard,
                        )
                    else:
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=caption,
                            reply_markup=keyboard,
                            disable_web_page_preview=True,
                        )
                return
            except TelegramForbiddenError:
                logger.info("User %s blocked the bot", user.telegram_id)
                return
            except TelegramBadRequest as exc:
                msg = str(exc).lower()
                if any(err in msg for err in ("chat not found", "deactivated", "blocked", "initiate conversation")):
                    logger.info("Delivery to %s skipped (user unavailable: %s)", user.telegram_id, exc)
                    return
                if photo_url:
                    # Картинка не скачалась/не подошла — текстом; URL ретраить
                    # бессмысленно, а вот текстовая отправка имеет смысл.
                    logger.info(
                        "sendPhoto to %s failed (%s) — falling back to text",
                        user.telegram_id,
                        exc,
                    )
                    photo_url = None
                    continue
                logger.warning(
                    "Deliver to %s bad request (attempt %d/3): %s",
                    user.telegram_id,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(1.5 * (attempt + 1))
            except TelegramAPIError as exc:
                logger.warning(
                    "Deliver to %s failed (attempt %d/3): %s",
                    user.telegram_id,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(1.5 * (attempt + 1))
        logger.error("Delivery to %s permanently failed", user.telegram_id)

    async def wait_pending(self) -> None:
        """Дожидается завершения всех фоновых отправок (для graceful shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["Notifier"]
