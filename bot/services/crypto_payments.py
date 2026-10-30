"""Оплата PRO криптой через Crypto Pay (@CryptoBot).

Поток платежа:
  1. Юзер жмет «Оплатить криптой» -> ``createInvoice`` -> ссылка на инвойс
     в @CryptoBot (валюта CRYPTO_ASSET, цена в USD для USDT);
  2. Юзер платит в @CryptoBot;
  3. Синхронизация: фоновая джоба и кнопка «Я уже оплатил» сверяют инвойсы
     через ``getInvoices`` -> оплаченный инвойс переводится в ``paid``,
     PRO активируется той же ``activate_pro()``, что и у Stars.

Идемпотентность: ``invoice_id`` — первичный ключ ``crypto_payments``;
повторная синхронизация оплаченного инвойса не начисляет PRO второй раз.

Webhook-режим не используем: локальный рантайм за туннелем с плавающим
URL, поэтому опрос API надежнее (GiftParser так же делает разовые платежи).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from bot.config import Settings
from bot.database import Database, activate_pro, award_referral
from bot.models import User

logger = logging.getLogger(__name__)

CRYPTO_PLANS = ("pro_month", "pro_lifetime")


class CryptoPayError(RuntimeError):
    """Ошибка обращения к Crypto Pay API (сеть, HTTP, отклоненный запрос)."""


class CryptoPayClient:
    """Тонкий клиент pay.crypt.bot (токен приложения Crypto Pay)."""

    def __init__(
        self,
        token: str,
        api_base: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._headers = {"Crypto-Pay-API-Token": token}
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, **params: Any) -> Any:
        try:
            resp = await self._client.post(
                f"{self._api_base}/{method}", json=params, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise CryptoPayError(f"{method}: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise CryptoPayError(f"{method}: non-JSON response") from exc
        if resp.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
            raise CryptoPayError(f"{method}: HTTP {resp.status_code} {str(data)[:200]}")
        return data.get("result")

    async def create_invoice(
        self, asset: str, amount: float, payload: str, description: str
    ) -> dict[str, Any]:
        """Создает инвойс; возвращает словарь Crypto Pay (invoice_id, url-ы)."""
        result = await self._call(
            "createInvoice",
            asset=asset,
            amount=round(float(amount), 2),
            payload=payload,
            description=description,
            allow_comments=False,
            allow_anonymous=False,
        )
        return result if isinstance(result, dict) else {}

    async def get_invoices(
        self,
        invoice_ids: list[int] | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Инвойсы приложения (опционально по ids/статусу)."""
        params: dict[str, Any] = {}
        if invoice_ids:
            params["invoice_ids"] = ",".join(str(i) for i in invoice_ids)
        if status:
            params["status"] = status
        result = await self._call("getInvoices", **params)
        items = result.get("items") if isinstance(result, dict) else None
        return [item for item in (items or []) if isinstance(item, dict)]

    @staticmethod
    def invoice_url(invoice: dict[str, Any]) -> str:
        """Ссылка на оплату: приоритет у Mini App-инвойса, затем бот/web."""
        for key in ("mini_app_invoice_url", "bot_invoice_url", "web_app_invoice_url", "pay_url"):
            url = str(invoice.get(key) or "")
            if url.startswith("https://"):
                return url
        return ""


class CryptoPaymentService:
    """Создание инвойсов и фоновая сверка оплат (как ProExpiryService)."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        bot: Any = None,
        *,
        client: CryptoPayClient | None = None,
        interval: float | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.bot = bot
        self.interval = interval if interval is not None else settings.CRYPTO_POLL_INTERVAL_SEC
        self.client = client or CryptoPayClient(settings.CRYPTO_PAY_TOKEN, settings.CRYPTO_API_BASE)
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        """Фича-флаг: крипто-оплату показываем только при заданном токене."""
        return bool(self.settings.CRYPTO_PAY_TOKEN)

    def price_for(self, plan: str) -> float:
        """Цена тарифа в USD для крипто-инвойса."""
        if plan == "pro_month":
            return self.settings.CRYPTO_MONTH_USD
        if plan == "pro_lifetime":
            return self.settings.CRYPTO_LIFETIME_USD
        raise ValueError(f"Unknown plan: {plan!r}")

    async def create_for_user(self, db_user: User, plan: str) -> tuple[int, str]:
        """Создает инвойс Crypto Pay и запись в БД; возвращает (invoice_id, url).

        Бросает ``CryptoPayError``/``ValueError`` — хендлер покажет юзеру
        аккуратный отказ, ничего в БД при ошибке не остается.
        """
        if not self.enabled:
            raise CryptoPayError("crypto payments disabled (CRYPTO_PAY_TOKEN is empty)")
        if plan not in CRYPTO_PLANS:
            raise ValueError(f"Unknown plan: {plan!r}")

        title = "GiftTracker PRO — 30 дней" if plan == "pro_month" else "GiftTracker PRO — Lifetime"
        invoice = await self.client.create_invoice(
            asset=self.settings.CRYPTO_ASSET,
            amount=self.price_for(plan),
            payload=plan,
            description=title,
        )
        invoice_id = int(invoice.get("invoice_id") or 0)
        if not invoice_id:
            raise CryptoPayError("createInvoice: empty invoice_id")

        await self.db.create_crypto_invoice(
            invoice_id=invoice_id,
            user_id=db_user.id,
            plan=plan,
            asset=str(invoice.get("asset") or self.settings.CRYPTO_ASSET),
            amount=float(invoice.get("amount") or self.price_for(plan)),
        )
        url = self.client.invoice_url(invoice)
        logger.info(
            "Crypto invoice #%d created: user=%s plan=%s amount=%s %s",
            invoice_id,
            db_user.telegram_id,
            plan,
            invoice.get("amount"),
            invoice.get("asset"),
        )
        return invoice_id, url

    async def settle_invoice(self, invoice_id: int) -> bool:
        """Инвойс оплачен -> статус ``paid`` + активация PRO + пуш юзеру.

        ``True`` — PRO начислен именно сейчас; ``False`` — инвойс не в БД,
        уже оплачен или юзер не найден (повторного начисления не будет).
        """
        current = await self.db.get_crypto_invoice(invoice_id)
        if current is None or current.status != "active":
            return False
        payment = await self.db.mark_crypto_paid(invoice_id)
        if payment is None:
            return False

        async with self.db.session() as session:
            user = await session.get(User, payment.user_id)
            if user is None:
                logger.error("Crypto invoice #%d: user %d not found", invoice_id, payment.user_id)
                return False
            await activate_pro(session, user, payment.plan, month_days=self.settings.PRO_MONTH_DAYS)
            telegram_id = user.telegram_id
            expires_text = (
                "навсегда 👑"
                if payment.plan == "pro_lifetime"
                else f"{self.settings.PRO_MONTH_DAYS} дней"
            )
            # Рефералка: награда пригласившему за крипто-оплату.
            reward = await award_referral(
                session, user, f"crypto:{payment.invoice_id}", "crypto"
            )
            referrer_tg = reward[0].telegram_id if reward is not None else None
            reward_days = reward[1] if reward is not None else 0.0

        await self._notify_paid(telegram_id, payment.amount, payment.asset, expires_text)
        if referrer_tg is not None:
            await self._notify_referrer(referrer_tg, reward_days)
        logger.info(
            "PRO activated via crypto: user=%s plan=%s invoice=#%d",
            telegram_id,
            payment.plan,
            invoice_id,
        )
        return True

    async def _notify_paid(self, telegram_id: int, amount: float, asset: str, expires_text: str) -> None:
        """Сообщает юзеру о зачислении (пуш может прийти и без его визита)."""
        if self.bot is None:
            return
        text = (
            "👑 <b>PRO активирован!</b>\n"
            f"Оплата криптой зачислена: <b>{amount:g} {asset}</b>\n"
            f"Действует: <b>{expires_text}</b>\n"
            "\n"
            "⚡ Пуши о жирных лотах теперь приходят мгновенно."
        )
        try:
            await self.bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001 — уведомление не должно ломать активацию
            logger.warning("Crypto payment notify failed for %s", telegram_id, exc_info=True)

    async def _notify_referrer(self, telegram_id: int, days: float) -> None:
        """Сообщает пригласившему о начислении за крипто-оплату друга."""
        if self.bot is None:
            return
        try:
            await self.bot.send_message(
                telegram_id,
                f"🤝 Твой приглашенный оплатил PRO — <b>+{days:g} дней</b> начислено автоматически!",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Referral notify failed for %s", telegram_id, exc_info=True)

    async def check_invoice_now(self, invoice_id: int) -> bool:
        """Точечная проверка по кнопке «Я уже оплатил»."""
        try:
            paid = await self.client.get_invoices(invoice_ids=[invoice_id], status="paid")
        except CryptoPayError as exc:
            logger.warning("Crypto check failed for invoice #%d: %s", invoice_id, exc)
            return False
        for item in paid:
            if int(item.get("invoice_id") or 0) == invoice_id:
                return await self.settle_invoice(invoice_id)
        return False

    async def sync_once(self) -> int:
        """Один проход: сверяет активные инвойсы с Crypto Pay.

        Возвращает число только что активированных PRO (уже оплаченные
        инвойсы пропускаются — идемпотентность на уровне БД).
        """
        if not self.enabled:
            return 0
        active = await self.db.active_crypto_invoices()
        if not active:
            return 0
        ids = [payment.invoice_id for payment in active]
        try:
            paid = await self.client.get_invoices(invoice_ids=ids, status="paid")
        except CryptoPayError as exc:
            logger.warning("Crypto sync failed: %s", exc)
            return 0
        paid_ids = {int(item.get("invoice_id") or 0) for item in paid}
        activated = 0
        for invoice_id in ids:
            if invoice_id in paid_ids and await self.settle_invoice(invoice_id):
                activated += 1
        return activated

    async def run(self) -> None:
        """Фоновый цикл синхронизации (запускается только при включенной фиче)."""
        if not self.enabled:
            logger.info("Crypto payments disabled (no CRYPTO_PAY_TOKEN) — sync loop not started")
            return
        logger.info("Crypto payment sync started (interval=%ss)", self.interval)
        while True:
            try:
                activated = await self.sync_once()
                if activated:
                    logger.info("Crypto sync activated %d PRO", activated)
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                logger.info("Crypto payment sync cancelled")
                raise
            except Exception:  # noqa: BLE001 — джоба не должна ронять бота
                logger.exception("Crypto payment sync failed")
                await asyncio.sleep(self.interval)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="crypto-payments")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await self.client.aclose()


__all__ = ["CRYPTO_PLANS", "CryptoPayClient", "CryptoPayError", "CryptoPaymentService"]
