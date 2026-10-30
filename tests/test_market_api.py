"""Тесты MarketAPI: пресеты с реальными NFT-медиа, кэш, mock-генератор."""
from __future__ import annotations

import asyncio

from bot.services.market_api import (
    GIFT_NAMES,
    GIFT_PRESETS,
    MarketAPI,
    fragment_model_image,
    getgems_collection_url,
    getgems_item_url,
)


class TestPresets:
    def test_eight_gifts_present(self):
        assert len(GIFT_PRESETS) == 8
        assert GIFT_NAMES[0] == "Plush Pepe"

    def test_gift_names_are_real_telegram_gifts(self):
        """Имена подарков — канонические, без выдуманных алиасов."""
        assert set(GIFT_NAMES) == {
            "Plush Pepe", "Genie Lamp", "Ion Gem", "Trapped Heart",
            "Astral Shard", "Heroic Helmet", "Mousse Cake", "Perfume Bottle",
        }

    def test_every_preset_has_real_media(self):
        for name, preset in GIFT_PRESETS.items():
            assert preset["image"].startswith("https://nft.fragment.com/"), name
            assert preset["base_floor"] > 0, name
            # Флоры по моделям берутся из датасета, а не из выдуманной
            # "коллекции" вида "Rare Meme".
            assert preset["models_path"].startswith("models/"), name
            assert preset["models_path"].endswith("/prices.json"), name
            assert "collection" not in preset, name

    def test_addresses_are_raw_ton(self):
        for name, preset in GIFT_PRESETS.items():
            address = preset.get("address")
            if address is not None:
                assert address.startswith("0:") and len(address) >= 60, name


class TestPerModelMedia:
    def test_fragment_model_image_url(self):
        assert fragment_model_image("Plush Pepe", 7) == "https://nft.fragment.com/gift/plushpepe-7.webp"

    def test_every_preset_has_fragment_slug_and_supply(self):
        for name, preset in GIFT_PRESETS.items():
            assert preset["fragment_slug"], name
            assert 1 <= preset["supply"] <= 4999, name

    async def test_mock_lots_are_specific_models(self, settings):
        """Лот — конкретный номер модели с per-item рендером Fragment."""
        market = MarketAPI(settings)
        seen = 0
        for _ in range(30):
            for lot in await market.get_listings():
                seen += 1
                slug = GIFT_PRESETS[lot.gift_name]["fragment_slug"]
                assert lot.image_url == f"https://nft.fragment.com/gift/{slug}-{lot.model_number}.webp"
                assert lot.model_number is not None and 1 <= lot.model_number <= GIFT_PRESETS[lot.gift_name]["supply"]
                assert lot.lot_number == f"#{lot.model_number}"
                assert lot.source == "getgems"
        assert seen > 0


class TestDeeplinks:
    def test_collection_url_with_address(self):
        url = getgems_collection_url("Plush Pepe")
        assert url.startswith("https://getgems.io/collection/EQ")

    def test_collection_url_falls_back_to_search(self):
        url = getgems_collection_url("Genie Lamp")
        assert url == "https://getgems.io/search?text=Genie%20Lamp"

    def test_item_url(self):
        assert getgems_item_url("0:abc") == "https://getgems.io/nft/0:abc"


class TestFloors:
    async def test_floors_return_all_gifts_with_media(self, settings):
        market = MarketAPI(settings)
        floors = await market.get_floors()
        assert set(floors.keys()) == set(GIFT_NAMES)
        for name, info in floors.items():
            assert info["floor"] > 0, name
            assert "change_24h" in info and "liquidity" in info
            assert info["image"].startswith("https://nft.fragment.com/"), name

    async def test_floors_cached_within_ttl(self, settings):
        market = MarketAPI(settings)
        first = await market.get_floors()
        second = await market.get_floors()
        assert first is second  # кэш 5 секунд возвращает тот же объект

    async def test_floors_walk_stays_in_band(self, settings):
        """Random-walk не выходит за ±25% от базового флора."""
        market = MarketAPI(settings)
        for _ in range(10):
            floors = await market.get_floors()
            # форсируем пересчет мимо кэша
            market._floors_cached_at = 0.0
            for name, info in floors.items():
                base = GIFT_PRESETS[name]["base_floor"]
                assert base * 0.74 < info["floor"] < base * 1.26, name

    async def test_concurrent_calls_are_safe(self, settings):
        market = MarketAPI(settings)
        results = await asyncio.gather(*(market.get_floors() for _ in range(10)))
        assert all(r.keys() == results[0].keys() for r in results)


class TestListings:
    async def test_listings_carry_real_media(self, settings):
        market = MarketAPI(settings)
        seen = 0
        for _ in range(30):  # генератор вероятностный — собираем выборку
            for lot in await market.get_listings():
                seen += 1
                assert lot.marketplace_url.startswith("https://getgems.io/collection/EQ") or \
                       lot.marketplace_url.startswith("https://getgems.io/search?text=")
                assert lot.image_url.startswith("https://nft.fragment.com/")
                assert lot.price > 0
        assert seen > 0, "mock-генератор не выдал ни одного лота за 30 опросов"
