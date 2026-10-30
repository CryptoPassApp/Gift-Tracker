"""Тесты этапа 3: жизненный цикл PRO, платежи Stars, админ-доход и /grant."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from bot.config import Settings
from bot.database import get_or_create_user
from bot.handlers import admin as admin_handler
from bot.handlers import subscription
from bot.models import Payment, User
from bot.services.admin_stats import (
    grant_pro,
    recent_payments,
    revenue_by_day,
    revenue_by_plan,
)
from bot.services.notifier import Notifier
from bot.services.pro_lifecycle import ProExpiryService
from tests.conftest import make_settings
from tests.test_monetization import FakeBot, make_signal


class FakeMessage:
    """Мини-двойник aiogram.Message: text + successful_payment + ответы."""

    def __init__(self, text: str = "", payment=None) -> None:
        self.text = text
        self.successful_payment = payment
        self.answers: list[str] = []
        self.bot = None
        self.chat = SimpleNamespace(id=1)
        self.message_id = 1

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append(text)


class FakePreCheckoutQuery:
    def __init__(self, payload: str, amount: int, currency: str = "XTR") -> None:
        self.invoice_payload = payload
        self.total_amount = amount
        self.currency = currency
        self.results: list[tuple[bool, str | None]] = []

    async def answer(self, ok: bool, error_message: str | None = None) -> None:
        self.results.append((ok, error_message))


class FakeEditMessage:
    def __init__(self) -> None:
        self.edited: str | None = None

    async def edit_text(self, text: str, **kwargs) -> None:
        self.edited = text


class FakeCallback:
    def __init__(self) -> None:
        self.message = FakeEditMessage()
        self.answers: list[tuple[str | None, dict]] = []

    async def answer(self, text: str | None = None, **kwargs) -> None:
        self.answers.append((text, kwargs))


def payment_info(charge: str = "charge-1", plan: str = "pro_month", amount: int = 150):
    return SimpleNamespace(
        invoice_payload=plan,
        telegram_payment_charge_id=charge,
        total_amount=amount,
    )


async def count_payments(db) -> int:
    async with db.session() as session:
        return int((await session.execute(select(func.count(Payment.id)))).scalar_one())


async def load_user(db, telegram_id: int) -> User:
    async with db.session() as session:
        return (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one()


class TestPreCheckout:
    async def test_valid_invoice_accepted(self, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        query = FakePreCheckoutQuery("pro_month", settings.PRO_MONTH_STARS)
        await subscription.on_pre_checkout(query)
        assert query.results == [(True, None)]

    async def test_wrong_amount_rejected(self, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        query = FakePreCheckoutQuery("pro_month", settings.PRO_MONTH_STARS - 1)
        await subscription.on_pre_checkout(query)
        assert query.results[0][0] is False

    async def test_unknown_payload_rejected(self, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        query = FakePreCheckoutQuery("pro_year", 150)
        await subscription.on_pre_checkout(query)
        assert query.results[0][0] is False

    async def test_non_xtr_currency_rejected(self, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        query = FakePreCheckoutQuery("pro_month", settings.PRO_MONTH_STARS, currency="USD")
        await subscription.on_pre_checkout(query)
        assert query.results[0][0] is False


class TestSuccessfulPayment:
    async def test_payment_persisted_and_pro_activated(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        message = FakeMessage(payment=payment_info())

        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7001})
            await subscription.on_successful_payment(message, session, user)

        stored = await load_user(db, 7001)
        assert stored.is_pro is True
        remaining = stored.pro_expires_at - datetime.utcnow()
        assert timedelta(days=29) < remaining <= timedelta(days=30)
        assert await count_payments(db) == 1
        assert "PRO активирован" in message.answers[-1]

    async def test_duplicate_charge_is_idempotent(self, db, tmp_path, monkeypatch):
        """Повторная доставка successful_payment не создает второй платеж."""
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)

        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7002})
            await subscription.on_successful_payment(
                FakeMessage(payment=payment_info("dup-charge")), session, user
            )

        before = (await load_user(db, 7002)).pro_expires_at
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7002})
            duplicate = FakeMessage(payment=payment_info("dup-charge"))
            await subscription.on_successful_payment(duplicate, session, user)

        after_user = await load_user(db, 7002)
        assert await count_payments(db) == 1
        assert after_user.pro_expires_at == before, "дубль не должен продлевать PRO"
        assert "уже зачислен" in duplicate.answers[-1]

    async def test_second_purchase_extends_subscription(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)

        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7003})
            await subscription.on_successful_payment(
                FakeMessage(payment=payment_info("extend-1")), session, user
            )
        first_expiry = (await load_user(db, 7003)).pro_expires_at

        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7003})
            await subscription.on_successful_payment(
                FakeMessage(payment=payment_info("extend-2")), session, user
            )

        assert await count_payments(db) == 2
        assert (await load_user(db, 7003)).pro_expires_at > first_expiry

    async def test_lifetime_sets_year_9999(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7004})
            await subscription.on_successful_payment(
                FakeMessage(payment=payment_info("life-1", "pro_lifetime", 999)), session, user
            )
        stored = await load_user(db, 7004)
        assert stored.is_pro is True
        assert stored.pro_expires_at.year == 9999

    async def test_month_after_lifetime_does_not_overflow(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7005})
            await subscription.on_successful_payment(
                FakeMessage(payment=payment_info("life-2", "pro_lifetime", 999)), session, user
            )
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7005})
            await subscription.on_successful_payment(
                FakeMessage(payment=payment_info("month-after-life")), session, user
            )
        stored = await load_user(db, 7005)
        assert stored.pro_expires_at.year == 9999
        assert await count_payments(db) == 2

    async def test_unknown_payload_not_persisted(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path)
        monkeypatch.setattr(subscription, "get_settings", lambda: settings)
        message = FakeMessage(payment=payment_info("bad-1", "pro_year", 150))
        async with db.session() as session:
            user = await get_or_create_user(session, {"id": 7006})
            await subscription.on_successful_payment(message, session, user)
        assert await count_payments(db) == 0
        assert "не распознан" in message.answers[-1]


class TestProExpiry:
    async def test_only_expired_subscriptions_are_downgraded(self, db):
        now = datetime.utcnow()
        async with db.session() as session:
            session.add_all(
                [
                    User(telegram_id=8001, is_pro=True, pro_expires_at=now - timedelta(days=1)),
                    User(telegram_id=8002, is_pro=True, pro_expires_at=now + timedelta(days=10)),
                    User(telegram_id=8003, is_pro=True, pro_expires_at=datetime(9999, 12, 31)),
                    User(telegram_id=8004, is_pro=True, pro_expires_at=None),
                ]
            )

        expired = await db.expire_pro_subscriptions(now=now)
        assert expired == 1
        assert (await load_user(db, 8001)).is_pro is False
        assert (await load_user(db, 8002)).is_pro is True
        assert (await load_user(db, 8003)).is_pro is True
        assert (await load_user(db, 8004)).is_pro is True

    async def test_service_runs_and_stops_cleanly(self, db, settings):
        service = ProExpiryService(db, settings, interval=0.01)
        assert await service.run_once() == 0
        await service.start()
        await asyncio.sleep(0.05)
        await service.stop()
        assert service._task is None

    async def test_expired_pro_loses_instant_push(self):
        settings = Settings(_env_file=None, FREE_PUSH_DELAY_SEC=3600)
        notifier = Notifier(settings)
        fake = FakeBot()
        notifier.bind(fake)  # type: ignore[arg-type]

        expired = User(
            telegram_id=8101,
            is_pro=True,
            pro_expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        await notifier.push_signal(make_signal(), [expired])
        await asyncio.sleep(0.05)
        assert fake.sent == [], "истекший PRO должен получать пуш с задержкой FREE"


class TestAdminStats:
    async def test_revenue_by_day_and_plan(self, db):
        now = datetime(2026, 10, 27, 12, 0, 0)
        async with db.session() as session:
            user = User(telegram_id=9001)
            session.add(user)
            await session.flush()
            session.add_all(
                [
                    Payment(user_id=user.id, telegram_charge_id="r1", stars_amount=150,
                            plan="pro_month", created_at=now),
                    Payment(user_id=user.id, telegram_charge_id="r2", stars_amount=150,
                            plan="pro_month", created_at=now - timedelta(days=1)),
                    Payment(user_id=user.id, telegram_charge_id="r3", stars_amount=999,
                            plan="pro_lifetime", created_at=now),
                ]
            )

        async with db.session() as session:
            daily = await revenue_by_day(session, days=7, now=now)
            by_plan = await revenue_by_plan(session)

        assert daily == [
            {"day": "2026-10-26", "payments": 1, "stars": 150},
            {"day": "2026-10-27", "payments": 2, "stars": 1149},
        ]
        assert by_plan["pro_month"] == {"payments": 2, "stars": 300}
        assert by_plan["pro_lifetime"] == {"payments": 1, "stars": 999}

    async def test_recent_payments_are_ordered_with_users(self, db):
        now = datetime.utcnow()
        async with db.session() as session:
            buyer = User(telegram_id=9002, username="buyer")
            session.add(buyer)
            await session.flush()
            for index in range(6):
                session.add(
                    Payment(
                        user_id=buyer.id,
                        telegram_charge_id=f"recent-{index}",
                        stars_amount=150,
                        plan="pro_month",
                        created_at=now + timedelta(minutes=index),
                    )
                )

        async with db.session() as session:
            payments = await recent_payments(session, limit=5)

        assert len(payments) == 5
        assert [p.telegram_charge_id for p in payments] == [
            "recent-5", "recent-4", "recent-3", "recent-2", "recent-1"
        ]
        assert all(p.user is not None and p.user.telegram_id == 9002 for p in payments)

    async def test_grant_pro_creates_user(self, db):
        async with db.session() as session:
            user = await grant_pro(session, 9003, "month", month_days=30)
            assert user.telegram_id == 9003 and user.is_pro is True
        stored = await load_user(db, 9003)
        assert timedelta(days=29) < stored.pro_expires_at - datetime.utcnow() <= timedelta(days=30)

    async def test_grant_pro_lifetime(self, db):
        async with db.session() as session:
            user = await grant_pro(session, 9004, "lifetime")
        assert user.pro_expires_at.year == 9999

    async def test_grant_pro_rejects_unknown_plan(self, db):
        async with db.session() as session:
            with pytest.raises(ValueError):
                await grant_pro(session, 9005, "year")

    async def test_admin_stats_handler_formats_revenue(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path, ADMIN_IDS=[111])
        monkeypatch.setattr(admin_handler, "get_settings", lambda: settings)
        async with db.session() as session:
            admin_user = await get_or_create_user(session, {"id": 111})
            buyer = User(telegram_id=9006, username="buyer")
            session.add(buyer)
            await session.flush()
            session.add(
                Payment(
                    user_id=buyer.id,
                    telegram_charge_id="stats-1",
                    stars_amount=150,
                    plan="pro_month",
                    created_at=datetime.utcnow(),
                )
            )
            callback = FakeCallback()
            await admin_handler.cb_admin_stats(callback, session, admin_user)

        assert callback.message.edited is not None
        assert "Доход по дням (7д)" in callback.message.edited
        assert "Последние платежи" in callback.message.edited
        assert "@buyer" in callback.message.edited


class TestGrantCommand:
    async def test_admin_can_grant_month(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path, ADMIN_IDS=[111])
        monkeypatch.setattr(admin_handler, "get_settings", lambda: settings)
        message = FakeMessage(text="/grant 222 month")
        async with db.session() as session:
            admin_user = await get_or_create_user(session, {"id": 111})
            await admin_handler.cmd_grant(message, session, admin_user)

        assert "PRO выдан" in message.answers[-1]
        target = await load_user(db, 222)
        assert target.is_pro is True

    async def test_non_admin_is_denied(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path, ADMIN_IDS=[999])
        monkeypatch.setattr(admin_handler, "get_settings", lambda: settings)
        message = FakeMessage(text="/grant 222 month")
        async with db.session() as session:
            sender = await get_or_create_user(session, {"id": 111})
            await admin_handler.cmd_grant(message, session, sender)

        assert "только для администраторов" in message.answers[-1]
        async with db.session() as session:
            assert (
                await session.execute(select(User).where(User.telegram_id == 222))
            ).scalar_one_or_none() is None

    async def test_bad_arguments_show_usage(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path, ADMIN_IDS=[111])
        monkeypatch.setattr(admin_handler, "get_settings", lambda: settings)
        message = FakeMessage(text="/grant abc month")
        async with db.session() as session:
            admin_user = await get_or_create_user(session, {"id": 111})
            await admin_handler.cmd_grant(message, session, admin_user)

        assert "Формат" in message.answers[-1]

    async def test_unknown_plan_shows_usage(self, db, tmp_path, monkeypatch):
        settings = make_settings(tmp_path, ADMIN_IDS=[111])
        monkeypatch.setattr(admin_handler, "get_settings", lambda: settings)
        message = FakeMessage(text="/grant 222 year")
        async with db.session() as session:
            admin_user = await get_or_create_user(session, {"id": 111})
            await admin_handler.cmd_grant(message, session, admin_user)

        assert "Формат" in message.answers[-1]
