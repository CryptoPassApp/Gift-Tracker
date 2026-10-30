"""Тесты PRO-фильтра дисконта: API настроек и поведение notifier'а."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from bot.models import User
from bot.services.notifier import Notifier
from bot.services.sniper_engine import Signal
from tests.conftest import make_settings, sign_init_data

USER = {"id": 910101, "first_name": "Filter", "username": "filter_tester"}


@pytest.fixture()
def client(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth(client) -> dict:
    token = client.app.state.settings.BOT_TOKEN
    return {"X-Init-Data": sign_init_data(token, USER)}


class TestSettingsApi:
    def test_default_settings_are_off(self, client, auth):
        body = client.get("/api/user/me", headers=auth).json()
        assert body["min_discount"] == 0.0
        assert body["effective_min_discount"] == 0.0

    def test_free_user_saves_threshold_but_it_stays_inactive(self, client, auth):
        resp = client.post("/api/user/settings", headers=auth, json={"min_discount": 25})
        assert resp.status_code == 200
        body = resp.json()
        assert body["min_discount"] == 25.0
        assert body["is_pro"] is False
        assert body["effective_min_discount"] == 0.0

        me = client.get("/api/user/me", headers=auth).json()
        assert me["min_discount"] == 25.0
        assert me["effective_min_discount"] == 0.0

    def test_pro_user_threshold_becomes_effective(self, client, auth):
        import asyncio

        from bot.database import Database, activate_pro

        settings = client.app.state.settings

        async def promote() -> None:
            db = Database(settings)
            async with db.session() as session:
                from sqlalchemy import select

                user = (await session.execute(select(User))).scalar_one_or_none()
                if user is None:
                    user = User(telegram_id=USER["id"], is_pro=False)
                    session.add(user)
                    await session.flush()
                await activate_pro(session, user, "pro_month")
            await db.dispose()

        # профиль создается первым авторизованным запросом
        client.get("/api/user/me", headers=auth)
        asyncio.run(promote())

        resp = client.post("/api/user/settings", headers=auth, json={"min_discount": 30})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_pro"] is True
        assert body["effective_min_discount"] == 30.0

    @pytest.mark.parametrize("value", [-1, 91, 1000])
    def test_out_of_range_rejected(self, client, auth, value):
        resp = client.post("/api/user/settings", headers=auth, json={"min_discount": value})
        assert resp.status_code == 422


class FakeBot:
    def __init__(self):
        self.sent: list[int] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append(chat_id)

    async def send_photo(self, chat_id: int, photo: str, caption: str = "", **kwargs):
        self.sent.append(chat_id)


def make_signal(discount: float) -> Signal:
    return Signal(
        gift_name="Plush Pepe",
        lot_number="#77",
        floor_price=16.5,
        deal_price=round(16.5 * (1 - discount / 100), 2),
        discount_pct=discount,
        net_profit_ton=1.0,
        marketplace_url="https://getgems.io/nft/0:probe",
        image_url="https://nft.fragment.com/gift/plushpepe-77.webp",
        item_address=None,
        source="getgems",
        is_hot=discount >= 30,
        created_at=datetime.utcnow(),
    )


def pro_user(telegram_id: int, threshold: float) -> User:
    return User(
        telegram_id=telegram_id,
        is_pro=True,
        pro_expires_at=datetime.utcnow() + timedelta(days=10),
        min_discount=threshold,
    )


class TestNotifierFilter:
    async def test_pro_threshold_skips_weaker_signals(self, tmp_path):
        settings = make_settings(tmp_path, FREE_PUSH_DELAY_SEC=0, BROADCAST_DELAY_SEC=0)
        notifier = Notifier(settings)
        bot = FakeBot()
        notifier.bind(bot)  # type: ignore[arg-type]

        await notifier.push_signal(
            make_signal(18.0),
            [pro_user(5001, 30.0), pro_user(5002, 15.0)],
        )
        await notifier.wait_pending()

        assert bot.sent == [5002], "сигнал −18% должен дойти только до порога 15%"

    async def test_zero_threshold_receives_everything(self, tmp_path):
        settings = make_settings(tmp_path, FREE_PUSH_DELAY_SEC=0)
        notifier = Notifier(settings)
        bot = FakeBot()
        notifier.bind(bot)  # type: ignore[arg-type]

        await notifier.push_signal(make_signal(16.0), [pro_user(5003, 0.0)])
        await notifier.wait_pending()

        assert bot.sent == [5003]

    async def test_free_user_threshold_is_ignored(self, tmp_path):
        settings = make_settings(tmp_path, FREE_PUSH_DELAY_SEC=0)
        notifier = Notifier(settings)
        bot = FakeBot()
        notifier.bind(bot)  # type: ignore[arg-type]

        free = User(telegram_id=5004, is_pro=False, min_discount=40.0)
        await notifier.push_signal(make_signal(16.0), [free])
        await notifier.wait_pending()

        assert bot.sent == [5004], "FREE не должен терять пуши из-за сохранённого порога"
