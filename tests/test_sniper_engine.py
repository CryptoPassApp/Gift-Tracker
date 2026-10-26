"""Интеграционные тесты движка снайпера: scan_once, дедупликация, матчинг алертов."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from bot.database import get_or_create_user
from bot.models import Alert, SnipeSignal
from bot.services.market_api import Lot, MarketAPI
from bot.services.notifier import Notifier
from bot.services.sniper_engine import SniperEngine, evaluate_lot


class RecordingMarket:
    """Рынок-двойник: отдает заданные лоты, флоры берет из пресетов."""

    def __init__(self, lots, floors):
        self._lots = lots
        self._floors = floors
        self.calls = 0

    async def get_floors(self):
        self.calls += 1
        return self._floors

    async def get_listings(self):
        return self._lots


def preset_floors() -> dict:
    return {
        name: {"floor": 16.5, "change_24h": 1.0, "liquidity": {"label": "HIGH", "value": "high"}}
        for name in ("Plush Pepe", "Magic Hat", "Spaceman")
    }


def hot_lot(url="https://getgems.io/nft/0:x1") -> Lot:
    # 16.5 -> 10.0 = -39% (Hot Liquidation)
    return Lot(gift_name="Plush Pepe", lot_number="#11", price=10.0, marketplace_url=url,
               image_url="https://nft.fragment.com/collection/plushpepe.webp")


async def make_engine(db, settings, market) -> tuple[SniperEngine, Notifier]:
    notifier = Notifier(settings)
    return SniperEngine(market=market, db=db, notifier=notifier, settings=settings), notifier


class TestScanOnce:
    async def test_signal_persisted_with_media(self, db, settings):
        market = RecordingMarket([hot_lot()], preset_floors())
        engine, _ = await make_engine(db, settings, market)

        signals = await engine.scan_once()
        assert len(signals) == 1
        assert signals[0].is_hot is True

        async with db.session() as session:
            rows = list((await session.execute(select(SnipeSignal))).scalars())
            assert len(rows) == 1
            assert rows[0].image_url == "https://nft.fragment.com/collection/plushpepe.webp"
            assert rows[0].discount_pct == 39.4

    async def test_same_lot_not_duplicated(self, db, settings):
        market = RecordingMarket([hot_lot()], preset_floors())
        engine, _ = await make_engine(db, settings, market)

        await engine.scan_once()
        await engine.scan_once()
        await engine.scan_once()

        async with db.session() as session:
            rows = list((await session.execute(select(SnipeSignal))).scalars())
            assert len(rows) == 1, "дедупликация по marketplace_url не сработала"

    async def test_sub_threshold_lot_ignored(self, db, settings):
        weak = Lot(gift_name="Magic Hat", lot_number="#12", price=23.0,
                   marketplace_url="https://getgems.io/nft/0:w")  # -13.3% < 15%
        market = RecordingMarket([weak], preset_floors())
        engine, _ = await make_engine(db, settings, market)

        assert await engine.scan_once() == []
        async with db.session() as session:
            assert list((await session.execute(select(SnipeSignal))).scalars()) == []

    async def test_hot_signal_pushed_to_all_users(self, db, settings):
        from tests.test_monetization import FakeBot

        market = RecordingMarket([hot_lot()], preset_floors())
        engine, notifier = await make_engine(db, settings, market)
        fake_bot = FakeBot()
        notifier.bind(fake_bot)  # type: ignore[arg-type]

        async with db.session() as session:
            await get_or_create_user(session, {"id": 5001})
            await get_or_create_user(session, {"id": 5002})

        await engine.scan_once()
        await notifier.wait_pending()
        assert len(fake_bot.sent) == 2

    async def test_personal_alert_match_notifies_owner(self, db, settings):
        from tests.test_monetization import FakeBot

        market = RecordingMarket([hot_lot()], preset_floors())
        engine, notifier = await make_engine(db, settings, market)
        fake_bot = FakeBot()
        notifier.bind(fake_bot)  # type: ignore[arg-type]

        async with db.session() as session:
            owner = await get_or_create_user(session, {"id": 6001})
            # Триггер: цена <= 12 TON и дисконт >= 20% — лот (10 TON, -39%) подходит.
            session.add(Alert(user_id=owner.id, gift_name="Plush Pepe",
                              max_price=12.0, min_discount=20.0))
            # Чужой алерт не должен сработать.
            session.add(Alert(user_id=owner.id, gift_name="Spaceman",
                              max_price=5.0, min_discount=15.0))

        await engine.scan_once()
        await notifier.wait_pending()
        # Один HOT-пуш + одно совпадение персонального алерта = 2 сообщения одному юзеру.
        assert len(fake_bot.sent) == 2
        assert any("алерт" in text.lower() for _, text in fake_bot.sent)


class TestEngineLoop:
    async def test_run_cancels_cleanly(self, db, settings):
        import asyncio

        market = RecordingMarket([], preset_floors())
        engine, _ = await make_engine(db, settings, market)
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert market.calls >= 1


class TestEvaluateReExport:
    def test_evaluate_lot_importable_from_engine(self):
        assert evaluate_lot is not None
