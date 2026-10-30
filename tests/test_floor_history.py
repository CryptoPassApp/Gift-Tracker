"""Тесты истории флоров: снимки, честное 24ч-изменение, ротация."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from api.routes.gifts import floors_payload
from bot.models import FloorSnapshot


class FakeMarket:
    """Рынок-двойник: отдает заранее заданные флоры."""

    def __init__(self, floors: dict) -> None:
        self._floors = floors

    async def get_floors(self) -> dict:
        return self._floors

    def get_catalog(self) -> dict:
        return {}


def floor_entry(floor: float) -> dict:
    return {
        "floor": floor,
        "change_24h": None,
        "liquidity": {"label": "MED", "value": "med"},
        "image": "https://nft.fragment.com/collection/plushpepe.webp",
        "address": None,
    }


class TestSnapshots:
    async def test_snapshot_is_written_once_per_interval(self, db):
        assert await db.record_floor_snapshot({"Plush Pepe": 100.0}, min_interval_sec=0) == 1
        # Второй вызов сразу — прорежен интервалом.
        assert await db.record_floor_snapshot({"Plush Pepe": 105.0}, min_interval_sec=3600) == 0

    async def test_snapshot_skips_empty_and_zero_floors(self, db):
        assert await db.record_floor_snapshot({}, min_interval_sec=0) == 0
        assert await db.record_floor_snapshot({"A": 0.0, "B": -5.0}, min_interval_sec=0) == 0


class TestChanges:
    async def test_no_change_without_old_history(self, db):
        await db.record_floor_snapshot({"Plush Pepe": 100.0}, min_interval_sec=0)
        changes = await db.floor_changes({"Plush Pepe": 110.0}, min_age_hours=20)
        assert changes["Plush Pepe"] is None, "нельзя показывать 24ч на данных 5-минутной давности"

    async def test_change_computed_against_oldest_snapshot(self, db):
        async with db.session() as session:
            session.add(
                FloorSnapshot(
                    gift_name="Plush Pepe",
                    floor=100.0,
                    created_at=datetime.utcnow() - timedelta(hours=21),
                )
            )
        changes = await db.floor_changes({"Plush Pepe": 110.0, "Ion Gem": 74.0}, min_age_hours=20)
        assert changes["Plush Pepe"] == 10.0
        assert changes["Ion Gem"] is None

    async def test_snapshots_older_than_window_are_ignored(self, db):
        async with db.session() as session:
            session.add(
                FloorSnapshot(
                    gift_name="Plush Pepe",
                    floor=50.0,
                    created_at=datetime.utcnow() - timedelta(hours=30),
                )
            )
        changes = await db.floor_changes({"Plush Pepe": 110.0}, window_hours=24)
        assert changes["Plush Pepe"] is None

    async def test_prune_removes_only_old_rows(self, db):
        async with db.session() as session:
            session.add(
                FloorSnapshot(
                    gift_name="Old", floor=1.0,
                    created_at=datetime.utcnow() - timedelta(days=10),
                )
            )
            session.add(FloorSnapshot(gift_name="Fresh", floor=1.0, created_at=datetime.utcnow()))
        assert await db.prune_floor_snapshots(7) == 1
        async with db.session() as session:
            remaining = list((await session.execute(select(FloorSnapshot))).scalars())
        assert [row.gift_name for row in remaining] == ["Fresh"]


class TestFloorsPayload:
    async def test_history_is_merged_into_floors(self, db, settings):
        settings.MARKET_SOURCE = "giftdataset"
        settings.FLOOR_SNAPSHOT_INTERVAL_SEC = 0
        async with db.session() as session:
            session.add(
                FloorSnapshot(
                    gift_name="Plush Pepe",
                    floor=100.0,
                    created_at=datetime.utcnow() - timedelta(hours=21),
                )
            )
        market = FakeMarket({"Plush Pepe": floor_entry(110.0)})

        payload = await floors_payload(market, settings, db)

        assert payload[0]["name"] == "Plush Pepe"
        assert payload[0]["change_24h"] == 10.0
        # Снимок текущего флора тоже записался.
        async with db.session() as session:
            rows = list((await session.execute(select(FloorSnapshot))).scalars())
        assert any(row.floor == 110.0 for row in rows)

    async def test_without_history_change_is_null(self, db, settings):
        settings.MARKET_SOURCE = "giftdataset"
        market = FakeMarket({"Ion Gem": floor_entry(74.0)})
        payload = await floors_payload(market, settings, db)
        assert payload[0]["change_24h"] is None
