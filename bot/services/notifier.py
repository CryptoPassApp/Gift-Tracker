"""Notifier — быстрая рассылка сигналов снайпера.

Правила монетизации (ЧАСТЬ 2 ТЗ):
  * PRO-пользователи получают пуши мгновенно (``asyncio.gather``-пачками);
  * FREE-пользователи — с задержкой ``FREE_PUSH_DELAY_SEC`` (45 секунд),
    чтобы самые жирные лоты перехватывали PRO-снайперы.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Settings
from bot.models import Alert, User

if TYPE_CHECKING:  # pragma: no cover
    from bot.services.sniper_engine import Signal

logger = logging.getLogger(__name__)


def _signal_text(signal: Signal) -> str:
    """Текст пуша о горячем лоте."""
    header = "🔥 HOT LIQUIDATION" if signal.is_hot else "⚡ SNIPE SIGNAL"
    return (
        f"{header} — <b>−{signal.discount_pct}% BELOW FLOOR</b>\n"
        f"\n"
        f"🎁 <b>{signal.gift_name}</b> · Lot {signal.lot_number}\n"
        f"💵 Buy Now: <b>{signal.deal_price} TON</b>\n"
        f"📈 Floor: <s>{signal.floor_price} TON</s>\n"
        f"💰 Profit: <b>+{signal.net_profit_ton} TON</b> <i>(net after fees)</i>"
    )


def _alert_text(signal: Signal, alert: Alert) -> str:
    """Текст пуша о сработавшем персональном алерте."""
    return (
        f"🔔 <b>Ваш алерт сработал!</b>\n"
        f"\n"
        f"🎁 <b>{signal.gift_name}</b> · Lot {signal.lot_number}\n"
        f"💵 Цена: <b>{signal.deal_price} TON</b> "
        f"(триггер: ≤ {alert.max_price} TON)\n"
        f"📉 Дисконт: <b>−{signal.discount_pct}%</b> "
        f"(порог: −{alert.min_discount}%)\n"
        f"💰 Profit: <b>+{signal.net_profit_ton} TON</b> net"
    )


def _snipe_keyboard(url: str, is_pro: bool) -> InlineKeyboardMarkup:
    """Кнопка покупки: PRO получает прямой диплинк Tonkeeper/Getgems."""
    button = InlineKeyboardButton(
        text=("⚡ Snipe via Tonkeeper" if is_pro else "⚡ Открыть лот"),
        url=url,
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


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
        """Рассылает горячий сигнал списку пользователей."""
        if not users:
            return
        for user in users:
            delay = 0.0 if user.is_pro_active() else float(self.settings.FREE_PUSH_DELAY_SEC)
            self._spawn(self._deliver(user, _signal_text(signal), signal.marketplace_url, delay))
        logger.info(
            "Signal %s (-%s%%) queued to %d user(s)",
            signal.gift_name,
            signal.discount_pct,
            len(users),
        )

    async def push_alert_matches(self, signal: Signal, alerts: list[Alert]) -> None:
        """Рассылает уведомления по персональным триггерам."""
        for alert in alerts:
            user = alert.user
            if user is None:
                continue
            delay = 0.0 if user.is_pro_active() else float(self.settings.FREE_PUSH_DELAY_SEC)
            self._spawn(self._deliver(user, _alert_text(signal, alert), signal.marketplace_url, delay))
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

    async def _deliver(self, user: User, text: str, url: str, delay: float) -> None:
        """Ждет задержку тарифа и отправляет сообщение (с ретраем)."""
        if delay > 0:
            await asyncio.sleep(delay)
        if self.bot is None:
            logger.error("Notifier has no bot bound — message dropped")
            return

        keyboard = _snipe_keyboard(url, user.is_pro_active())
        for attempt in range(3):
            try:
                async with self._semaphore:
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=text,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                return
            except TelegramForbiddenError:
                logger.info("User %s blocked the bot", user.telegram_id)
                return
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
