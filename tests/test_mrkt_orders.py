"""MRKT-ордера автобай: парсинг бида и апгрейд эталона в reference_price.

Ордер MRKT — ставка покупателя («куплю подарок коллекции X за до N TON»).
Лот, дешевле такого бида, — мгновенная связка: купил на входе, продал в MRKT,
поэтому эталон оценки поднимается до топ-бида. Ордера качает хук
/api/internal/mrkt-init в data/mrkt_bids.json — сканер читает файл.
"""
from __future__ import annotations

import json
import time

import pytest

from bot.services.market_api import Lot, MarketAPI, mrkt_order_url
from bot.services.sniper_engine import build_signal

SAMPLE_ORDERS = [
    {
        "id": "fadc6e0d-8817-4e06-a55f-b1317e0c649c",
        "collectionName": "Electric Skull",
        "priceMinNanoTONs": 500000000,
        "priceMaxNanoTONs": 32360410777,
    },
    {
        "id": "8a1bd14c-e242-4e36-a3f4-c604d7782659",
        "collectionName": "Electric Skull",
        "priceMinNanoTONs": 500000000,
        "priceMaxNanoTONs": 100000000000,  # выше — этот и есть топ-бид
    },
    {
        "id": "deadbeef-0000-0000-0000-000000000000",
        "collectionName": "Мусор без совпадения",
        "priceMinNanoTONs": 1,
        "priceMaxNanoTONs": 999000000000,
    },
    {
        "id": "broken-price",
        "collectionName": "Genie Lamp",
        "priceMaxNanoTONs": "не число",
    },
]


@pytest.fixture
def market(settings, tmp_path) -> MarketAPI:
    # Пустой файл бида по умолчанию: тесты детерминированы, живые данные
    # из data/mrkt_bids.json не протекают.
    bids_file = tmp_path / "mrkt_bids.json"
    bids_file.write_text(json.dumps({"orders": [], "ts": time.time()}), encoding="utf-8")
    settings.MRKT_BIDS_FILE = str(bids_file)

    api = MarketAPI(settings)
    # Каталог в тестах пуст — подменяем сопоставление имён на «как есть».
    api._gift_by_name = lambda name: name if name in {"Electric Skull", "Genie Lamp"} else None  # type: ignore[method-assign]
    return api


class TestMrktOrders:
    async def test_top_bids_take_max_per_collection(self, market: MarketAPI):
        bids_file = market.settings.MRKT_BIDS_FILE
        with open(bids_file, "w", encoding="utf-8") as f:
            json.dump({"orders": SAMPLE_ORDERS, "ts": time.time()}, f)

        bids = await market._mrkt_top_bids()

        assert bids["Electric Skull"]["price"] == pytest.approx(100.0)
        assert bids["Electric Skull"]["order_id"] == "8a1bd14c-e242-4e36-a3f4-c604d7782659"
        assert "Мусор без совпадения" not in bids
        assert "Genie Lamp" not in bids, "битая цена не должна попадать в бида"

    async def test_stale_bids_file_is_ignored(self, market: MarketAPI):
        bids_file = market.settings.MRKT_BIDS_FILE
        with open(bids_file, "w", encoding="utf-8") as f:
            json.dump({"orders": SAMPLE_ORDERS, "ts": time.time() - 3600}, f)

        assert await market._mrkt_top_bids() == {}

    async def test_reference_price_upgrades_to_mrkt_bid(self, market: MarketAPI):
        lot = Lot(
            gift_name="Electric Skull",
            lot_number="#7",
            price=50.0,
            marketplace_url="https://getgems.io/nft/EQx",
            source="getgems",
        )
        floors = {"Electric Skull": {"floor": 40.0, "change_24h": None, "liquidity": {"label": "MED", "value": "med"}}}

        async def fake_bids():
            return {"Electric Skull": {"price": 100.0, "order_id": "x"}}

        market._mrkt_top_bids = fake_bids  # type: ignore[method-assign]
        reference, basis = await market.reference_price(lot, floors)
        assert basis == "mrkt_bid"
        assert reference == pytest.approx(100.0)

    async def test_reference_price_keeps_floor_when_bid_lower(self, market: MarketAPI):
        lot = Lot(
            gift_name="Electric Skull",
            lot_number="#7",
            price=30.0,
            marketplace_url="https://getgems.io/nft/EQx",
            source="getgems",
        )
        floors = {"Electric Skull": {"floor": 80.0, "change_24h": None, "liquidity": {"label": "MED", "value": "med"}}}

        async def fake_bids():
            return {"Electric Skull": {"price": 50.0, "order_id": "x"}}

        market._mrkt_top_bids = fake_bids  # type: ignore[method-assign]
        reference, basis = await market.reference_price(lot, floors)
        assert basis == "collection"
        assert reference == pytest.approx(80.0)

    async def test_reference_price_without_bids_keeps_floor(self, market: MarketAPI):
        lot = Lot(
            gift_name="Electric Skull",
            lot_number="#7",
            price=30.0,
            marketplace_url="https://getgems.io/nft/EQx",
            source="getgems",
        )
        floors = {"Electric Skull": {"floor": 80.0, "change_24h": None, "liquidity": {"label": "MED", "value": "med"}}}
        # Файл бида пуст (фикстура) — эталон остается флором коллекции.
        reference, basis = await market.reference_price(lot, floors)
        assert basis == "collection"
        assert reference == pytest.approx(80.0)

    def test_build_signal_mrkt_bid_uses_mrkt_fee(self, settings):
        # Net = 100 * (1 - 0.05) - 60 - 0.08 = 34.92 (MRKT_FEE=5%)
        lot = Lot(
            gift_name="Electric Skull",
            lot_number="#7",
            price=60.0,
            marketplace_url="https://getgems.io/nft/EQx",
            source="getgems",
        )
        signal = build_signal(lot, 100.0, settings, floor_basis="mrkt_bid")
        assert signal is not None
        assert signal.net_profit_ton == pytest.approx(34.92)

    def test_mrkt_order_url_strips_dashes(self):
        assert mrkt_order_url("b8f5d267-e3e4-4578-a333-88d2df8d0669") == (
            "https://t.me/mrkt/app?startapp=b8f5d267e3e44578a33388d2df8d0669"
        )

    def test_push_caption_shows_mrkt_bid_line(self, settings):
        from bot.services.notifier import _signal_caption

        signal = build_signal(
            Lot(
                gift_name="Electric Skull",
                lot_number="#7",
                price=60.0,
                marketplace_url="https://getgems.io/nft/EQx",
                source="getgems",
            ),
            100.0,
            settings,
            floor_basis="mrkt_bid",
        )
        caption = _signal_caption(signal)
        assert "MRKT автобай" in caption
        assert "100 TON" in caption
