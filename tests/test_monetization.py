"""Тесты монетизации: лимиты FREE, активация PRO, нотификатор с задержками."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from bot.config import Settings
from bot.database import activate_pro, count_active_alerts, get_or_create_user
from bot.models import Alert, User
from bot.services.notifier import Notifier
from bot.services.sniper_engine import Signal


def make_signal(**overrides) -> Signal:
    defaults = dict(
        gift_name="Plush Pepe",
        lot_number="#42",
        floor_price=16.5,
        deal_price=11.0,
        discount_pct=33.3,
        net_profit_ton=4.8,
        marketplace_url="https://getgems.io/collection/x",
        image_url="https://nft.fragment.com/collection/plushpepe.webp",
        item_address=None,
        source="getgems",
        is_hot=True,
        created_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    return Signal(**defaults)


class FakeBot:
    """Минимальный двойник aiogram.Bot: пишет отправленные сообщения в список."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append((chat_id, text))

    async def copy_message(self, chat_id: int, **kwargs):
        self.sent.append((chat_id, "<copy>"))


class TestUserLifecycle:
    async def test_get_or_create_user_creates_once(self, db):
        async with db.session() as session:
            first = await get_or_create_user(session, {"id": 1, "username": "a"})
            second = await get_or_create_user(session, {"id": 1, "username": "a"})
            assert first.id == second.id

    async def test_expired_pro_downgrades_on_touch(self, db):
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7})
            user.is_pro = True
            user.pro_expires_at = datetime.utcnow() - timedelta(days=1)
            refreshed = await get_or_create_user(session, {"id": 7})
            assert refreshed.is_pro is False
            assert refreshed.is_pro_active() is False


class TestProActivation:
    async def test_month_adds_30_days(self, db):
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 10})
            await activate_pro(session, user, "pro_month", month_days=30)
            assert user.is_pro is True
            remaining = user.pro_expires_at - datetime.utcnow()
            assert timedelta(days=29) < remaining <= timedelta(days=30)

    async def test_month_renewal_stacks(self, db):
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 11})
            await activate_pro(session, user, "pro_month", month_days=30)
            await activate_pro(session, user, "pro_month", month_days=30)
            remaining = user.pro_expires_at - datetime.utcnow()
            assert remaining > timedelta(days=59)

    async def test_lifetime_never_expires(self, db):
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 12})
            await activate_pro(session, user, "pro_lifetime")
            assert user.pro_expires_at.year == 9999
            assert user.is_pro_active() is True


class TestAlertsQuota:
    async def test_free_limit_is_one(self, db, settings):
        assert settings.FREE_ALERTS_LIMIT == 1
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 20})
            session.add(Alert(user_id=user.id, gift_name="Plush Pepe", max_price=12.0))
            await session.flush()
            assert await count_active_alerts(session, user) == 1

    async def test_pro_has_no_limit(self, db):
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 21})
            user.is_pro = True
            for i in range(5):
                session.add(Alert(user_id=user.id, gift_name="Spaceman", max_price=30.0 + i))
            await session.flush()
            assert await count_active_alerts(session, user) == 5


class TestNotifier:
    async def test_pro_and_free_deliver_with_their_delays(self):
        settings = Settings(_env_file=None, FREE_PUSH_DELAY_SEC=0, BROADCAST_DELAY_SEC=0)
        notifier = Notifier(settings)
        fake = FakeBot()
        notifier.bind(fake)  # type: ignore[arg-type]

        pro = User(telegram_id=1001, is_pro=True, pro_expires_at=datetime(9999, 12, 31))
        free = User(telegram_id=1002, is_pro=False)
        signal = make_signal()

        await notifier.push_signal(signal, [pro, free])
        await notifier.wait_pending()

        chat_ids = {chat for chat, _ in fake.sent}
        assert chat_ids == {1001, 1002}
        assert all("33.3" in text or "PROFIT" in text.upper() or "TON" in text for _, text in fake.sent)

    async def test_free_delay_is_scheduled_not_immediate(self):
        settings = Settings(_env_file=None, FREE_PUSH_DELAY_SEC=3600)
        notifier = Notifier(settings)
        fake = FakeBot()
        notifier.bind(fake)  # type: ignore[arg-type]

        free = User(telegram_id=2002, is_pro=False)
        await notifier.push_signal(make_signal(), [free])
        await asyncio.sleep(0.05)
        assert fake.sent == [], "FREE не должен получать пуш до истечения задержки"

    async def test_blocked_user_is_skipped_silently(self):
        from aiogram.exceptions import TelegramForbiddenError

        settings = Settings(_env_file=None, FREE_PUSH_DELAY_SEC=0)
        notifier = Notifier(settings)

        class BlockingBot(FakeBot):
            async def send_message(self, chat_id, text, **kwargs):
                raise TelegramForbiddenError(method="sendMessage", message="blocked")

        notifier.bind(BlockingBot())  # type: ignore[arg-type]
        await notifier.push_signal(make_signal(), [User(telegram_id=3003)])
        await notifier.wait_pending()  # не должно упасть

    async def test_broadcast_admin_message_counts(self):
        settings = Settings(_env_file=None, BROADCAST_DELAY_SEC=0)
        notifier = Notifier(settings)
        fake = FakeBot()
        users = [User(telegram_id=i) for i in (1, 2, 3)]
        sent = await notifier.broadcast_admin_message(fake, from_chat_id=1, message_id=1, users=users)  # type: ignore[arg-type]
        assert sent == 3
