"""Тесты реального рыночного источника: каталог всех NFT, флоры и лоты TonAPI."""
from __future__ import annotations

import time
from collections import deque

from bot.services.market_api import (
    GIFT_NAMES,
    GIFT_PRESETS,
    Lot,
    MarketAPI,
    TraitPrices,
    extract_traits,
    fragment_model_image,
    getgems_collection_url,
)


def _dataset_payload() -> dict:
    """Мини-копия Gifts_Details.json: 8 пресетов + один подарок вне пресетов."""
    entries = []
    for index, preset in enumerate(GIFT_PRESETS.values()):
        entries.append(
            {
                "full_name": preset["dataset_name"],
                "short_name": preset["fragment_slug"],
                "regular_id": str(1000 + index),
                "getgems_price_ton": 100.0 + index,
                "tgmrkt_price_ton": 90.0 + index,
                "portal_price_ton": 95.0 + index,
                "floor_price_ton": 90.0 + index,
            }
        )
    entries.append(
        {
            "full_name": "Artisan Brick",
            "short_name": "artisan_brick",
            "regular_id": "123",
            "getgems_price_ton": 73.78,
            "tgmrkt_price_ton": 74.46,
            "portal_price_ton": 73.78,
            "floor_price_ton": 73.78,
        }
    )
    return {"upgraded": entries, "unupgraded": []}


class TestDatasetCatalog:
    def test_full_catalog_mapped(self, settings):
        market = MarketAPI(settings)
        floors = market._parse_giftdataset_floors(_dataset_payload())
        assert floors is not None
        assert set(GIFT_NAMES).issubset(set(floors))
        assert "Artisan Brick" in floors
        assert "Artisan Brick" in market.get_catalog()

    def test_best_floor_is_min_across_markets(self, settings):
        market = MarketAPI(settings)
        floors = market._parse_giftdataset_floors(_dataset_payload())
        assert floors["Plush Pepe"]["floor"] == 90.0  # tgmrkt дешевле getgems/portals
        assert floors["Plush Pepe"]["market_floors"]["getgems"] == 100.0
        assert floors["Plush Pepe"]["market_floors"]["tgmrkt"] == 90.0

    def test_dynamic_gift_uses_cloud_image(self, settings):
        market = MarketAPI(settings)
        market._parse_giftdataset_floors(_dataset_payload())
        entry = market.get_catalog()["Artisan Brick"]
        assert entry["image"].endswith("/webp/by_id/123.webp")
        assert entry["address"] is None
        assert entry["address_verified"] is False
        assert entry["fragment_slug"] == "artisanbrick"

    def test_missing_dataset_entries_are_skipped(self, settings):
        market = MarketAPI(settings)
        assert market._parse_giftdataset_floors({"upgraded": []}) is None


class TestTonapiLots:
    def test_sale_item_becomes_real_lot_with_traits(self, settings):
        market = MarketAPI(settings)
        items = [
            {
                "address": "0:abc",
                "metadata": {
                    "name": "Plush Pepe #1579",
                    "attributes": [
                        {"trait_type": "Model", "value": "Pepemint"},
                        {"trait_type": "Backdrop", "value": "Moonstone"},
                        {"trait_type": "Symbol", "value": "Pegasus"},
                    ],
                },
                "sale": {
                    "price": {"value": "17500000000000"},
                    "market": {"name": "Getgems Sales"},
                },
            },
            {"address": "0:def", "metadata": {"name": "Plush Pepe #2"}},
        ]
        lots = market._parse_tonapi_items(items, "Plush Pepe", GIFT_PRESETS["Plush Pepe"])
        assert len(lots) == 1
        lot = lots[0]
        assert lot.price == 17500.0
        assert lot.lot_number == "#1579"
        assert lot.model_number == 1579
        assert lot.item_address == "0:abc"
        assert lot.marketplace_url.startswith("https://getgems.io/nft/0:abc")
        assert lot.image_url == fragment_model_image("Plush Pepe", 1579)
        assert lot.source == "getgems"
        # Атрибуты конкретного NFT — то, чем один Pepe отличается от другого.
        assert lot.model == "Pepemint"
        assert lot.backdrop == "Moonstone"
        assert lot.symbol == "Pegasus"

    def test_item_without_attributes_has_empty_traits(self, settings):
        market = MarketAPI(settings)
        items = [{
            "address": "0:x",
            "metadata": {"name": "Ion Gem #7"},
            "sale": {"price": {"value": "80000000000"},
                     "market": {"name": "Getgems Sales"}},
        }]
        lot = market._parse_tonapi_items(items, "Ion Gem", GIFT_PRESETS["Ion Gem"])[0]
        assert lot.model is None and lot.backdrop is None and lot.symbol is None

    def test_non_getgems_market_sale_is_skipped(self, settings):
        """Мертвые контракты сторонних маркетов не становятся «сделками -95%».

        Реальный случай: Scared Cat #15630, «15 TON при флоре 285» — контракт
        Marketapp Marketplace с февраля, все попытки покупки провалены, а
        парсер подписывал его getgems и пушил в ленту.
        """
        market = MarketAPI(settings)

        def _item(market_name: str, num: int) -> dict:
            return {
                "address": f"0:{num}",
                "metadata": {"name": f"Scared Cat #{num}"},
                "sale": {
                    "type": "Sale",
                    "price": {"value": str(int(15.0 * 1e9))},
                    "market": {"name": market_name},
                },
            }

        lots = market._parse_tonapi_items(
            [
                _item("Marketapp Marketplace", 1),   # призрак — мимо
                _item("Getgems Sales", 2),           # настоящий getgems — берём
                _item("Getgems", 3),                 # короткий вариант — берём
                _item("", 4),                        # маркет не указан — мимо
            ],
            "Scared Cat",
            {},
        )
        assert [lot.lot_number for lot in lots] == ["#2", "#3"]

    def test_floor_prices_feed_reference_median(self, settings):
        market = MarketAPI(settings)
        market._price_samples["Plush Pepe"] = deque([100, 110, 120, 130, 140], maxlen=200)
        assert market.reference_floor("Plush Pepe", 999.0) == 120.0
        assert market.reference_floor("Ion Gem", 999.0) == 999.0


class TestTonapiFloorMinMerge:
    """Срез пагинации TonAPI не задирает флор Getgems (баг «-57% при флоре 18.8»).

    Минимум по первым 100-200 айтемам (порядок минта) — не флор: дешевые лоты
    лежат глубже. Эталон из датасета можно только понижать до реально
    увиденной цены, иначе честные лоты выглядят фейковыми сделками.
    """

    @staticmethod
    def _items(*prices_ton: float) -> list[dict]:
        return [
            {
                "address": f"0:{i}",
                "metadata": {"name": f"Ice Cream #{i}"},
                "sale": {"price": {"value": str(int(p * 1e9))},
                         "market": {"name": "Getgems Sales"}},
            }
            for i, p in enumerate(prices_ton)
        ]

    def _market_with_floor(self, settings, getgems_floor: float) -> MarketAPI:
        market = MarketAPI(settings)
        info = {"floor": 3.9, "market_floors": {"getgems": getgems_floor}}
        market._catalog = {"Ice Cream": dict(info)}
        market._floors_cache = {"Ice Cream": dict(info)}
        return market

    def test_slice_min_never_raises_floor(self, settings):
        # Датасет говорит флор 3.9; срез увидел лоты 8 и 18.8 —
        # запись 18.8 вместо 3.9 и создавала фейковые «-57%».
        market = self._market_with_floor(settings, 3.9)
        market._parse_tonapi_items(self._items(8.0, 18.8), "Ice Cream", {})
        assert market._floors_cache["Ice Cream"]["market_floors"]["getgems"] == 3.9
        assert market._catalog["Ice Cream"]["market_floors"]["getgems"] == 3.9

    def test_slice_min_lowers_floor_to_seen_price(self, settings):
        # Датасет устал (старый флор 20), а реально виден лот за 18.8 —
        # флор честно опускается до увиденной цены.
        market = self._market_with_floor(settings, 20.0)
        market._parse_tonapi_items(self._items(18.8, 25.0), "Ice Cream", {})
        assert market._floors_cache["Ice Cream"]["market_floors"]["getgems"] == 18.8

    def test_floor_write_without_cache_is_noop(self, settings):
        # Никакого кэша еще нет (старт процесса) — запись не падает.
        market = MarketAPI(settings)
        lots = market._parse_tonapi_items(self._items(8.0), "Ice Cream", {})
        assert len(lots) == 1


class TestTraitParsing:
    def test_extract_traits_is_case_insensitive(self):
        traits = extract_traits(
            {"attributes": [
                {"trait_type": "model", "value": "Barcelona"},
                {"trait_type": "Backdrop", "value": " Lemongrass "},
                {"trait_type": "Symbol", "value": "Cap"},
                {"trait_type": "Unknown", "value": "x"},
            ]}
        )
        assert traits == {"model": "Barcelona", "backdrop": "Lemongrass", "symbol": "Cap"}

    def test_extract_traits_ignores_broken_entries(self):
        traits = extract_traits({"attributes": [None, {"trait_type": "Model"}, {"value": "Frozen"}]})
        assert traits == {"model": None, "backdrop": None, "symbol": None}


class TestModelReferencePrice:
    """Эталон лота — флор его модели, а не общий флор коллекции."""

    def _floors(self) -> dict:
        return {
            "Plush Pepe": {
                "floor": 6470.0,
                "market_floors": {"getgems": 6470.0, "tgmrkt": 7140.0, "portals": 6470.0},
            }
        }

    def _market_with_models(self, settings, models: dict) -> MarketAPI:
        market = MarketAPI(settings)
        market._trait_prices["Plush Pepe"] = TraitPrices(
            models=models, fetched_at=time.monotonic()
        )
        return market

    async def test_model_floor_above_collection_floor_is_used(self, settings):
        market = self._market_with_models(settings, {"Leonardo": 25500.0})
        lot = Lot(
            gift_name="Plush Pepe", lot_number="#1668", price=17500.0,
            marketplace_url="https://getgems.io/nft/0:x", model="Leonardo",
        )
        reference, basis = await market.reference_price(lot, self._floors())
        assert reference == 25500.0
        assert basis == "model:Leonardo"

    async def test_cheap_model_stays_on_collection_floor(self, settings):
        market = self._market_with_models(settings, {"Hue Jester": 5304.0})
        lot = Lot(
            gift_name="Plush Pepe", lot_number="#1", price=6000.0,
            marketplace_url="https://getgems.io/nft/0:y", model="Hue Jester",
        )
        reference, basis = await market.reference_price(lot, self._floors())
        assert reference == 6470.0  # getgems-флор, а не 7140 с TGMrkt
        assert basis == "collection"

    async def test_unknown_model_stays_on_collection_floor(self, settings):
        market = self._market_with_models(settings, {"Leonardo": 25500.0})
        lot = Lot(
            gift_name="Plush Pepe", lot_number="#2", price=6000.0,
            marketplace_url="https://getgems.io/nft/0:z", model="Unknown Model",
        )
        assert await market.reference_price(lot, self._floors()) == (6470.0, "collection")

    async def test_absurd_model_floor_is_capped(self, settings):
        """Мусорное значение в датасете не должно давать фейковый мега-дисконт."""
        market = self._market_with_models(settings, {"Leonardo": 1_000_000.0})
        lot = Lot(
            gift_name="Plush Pepe", lot_number="#3", price=17500.0,
            marketplace_url="https://getgems.io/nft/0:w", model="Leonardo",
        )
        assert await market.reference_price(lot, self._floors()) == (6470.0, "collection")

    async def test_non_pepe_gift_model_stays_on_collection_floor(self, settings):
        """Для обычных подарков (Mini Oscar и др.) эталон ВСЕГДА флор коллекции, а не модели."""
        market = self._market_with_models(settings, {"Red Carpet": 346.8})
        floors = {
            "Mini Oscar": {
                "floor": 75.0,
                "market_floors": {"getgems": 125.0, "portals": 76.84},
            }
        }
        lot = Lot(
            gift_name="Mini Oscar",
            lot_number="#3191",
            price=150.0,
            marketplace_url="https://getgems.io/nft/0:oscar",
            model="Red Carpet",
            source="getgems",
        )
        ref, basis = await market.reference_price(lot, floors)
        assert ref == 125.0  # Реальный Getgems флор, а не 346.8
        assert basis == "collection"


class TestVerifiedAddresses:
    def test_verified_collection_links_to_getgems(self):
        assert getgems_collection_url("Plush Pepe").startswith(
            "https://getgems.io/collection/EQ"
        )
        assert getgems_collection_url("Ion Gem").startswith(
            "https://getgems.io/collection/EQ"
        )

    def test_unverified_collection_uses_search(self):
        assert getgems_collection_url("Astral Shard").startswith(
            "https://getgems.io/search?text="
        )


class TestTonapiKeyHeader:
    """Краулер лотов TonAPI обязан ходить с ключом: на выделенном IP
    анонимный пул выжжен и каждый запрос получает 429 (баг «сканер сдох»)."""

    def test_key_attached_when_set(self, settings, monkeypatch):
        settings.TONAPI_KEY = "test-key-123"
        market = MarketAPI(settings)
        headers = market._tonapi_headers()
        assert headers["Authorization"] == "Bearer test-key-123"

    def test_no_header_without_key(self, settings):
        settings.TONAPI_KEY = ""
        market = MarketAPI(settings)
        assert "Authorization" not in market._tonapi_headers()


class TestModelAskStore:
    """Реально увиденный аск модели понижает «флор» датасета.

    Датасет писал зависшие хотелки (Yellow Hug «25498» при висящем
    непроданным лоте за 7500) — из-за них лента полнилась фейковыми
    «сделками -70%» по одним и тем же пепкам.
    """

    @staticmethod
    def _items(*models_prices: tuple) -> list[dict]:
        items = []
        for i, (model, p) in enumerate(models_prices):
            items.append({
                "address": f"0:{i}",
                "metadata": {
                    "name": f"Plush Pepe #{i}",
                    "attributes": [{"trait_type": "Model", "value": model}],
                },
                "sale": {"price": {"value": str(int(p * 1e9))},
                         "market": {"name": "Getgems Sales"}},
            })
        return items

    def test_parse_records_min_ask_per_model(self, settings):
        market = MarketAPI(settings)
        market._parse_tonapi_items(
            self._items(("Yellow Hug", 8000.0), ("Yellow Hug", 7500.0)), "Plush Pepe", {}
        )
        assert market._model_asks["Plush Pepe"]["Yellow Hug"]["floor"] == 7500.0

    def test_ask_never_rises_on_rescan(self, settings):
        market = MarketAPI(settings)
        market._record_model_asks("Plush Pepe", {"Yellow Hug": 7500.0})
        market._record_model_asks("Plush Pepe", {"Yellow Hug": 9000.0})
        assert market._model_asks["Plush Pepe"]["Yellow Hug"]["floor"] == 7500.0

    def test_asks_survive_restart_from_file(self, settings):
        market = MarketAPI(settings)
        market._record_model_asks("Plush Pepe", {"Stripes": 6988.0})
        restarted = MarketAPI(settings)
        assert restarted._observed_model_floor("Plush Pepe", "Stripes") == 6988.0

    async def test_observed_ask_overrides_dataset_floor(self, settings):
        """Аск 7500, увиденный краулером, режет датасетные 25500 до 7500."""
        market = MarketAPI(settings)
        market._trait_prices["Plush Pepe"] = TraitPrices(
            models={"Yellow Hug": 25500.0}, fetched_at=time.monotonic()
        )
        market._record_model_asks("Plush Pepe", {"Yellow Hug": 7500.0})
        lot = Lot(
            gift_name="Plush Pepe", lot_number="#9", price=7000.0,
            marketplace_url="https://getgems.io/nft/0:q", model="Yellow Hug",
        )
        floors = {"Plush Pepe": {
            "floor": 6470.0, "market_floors": {"getgems": 6470.0},
        }}
        assert await market.reference_price(lot, floors) == (7500.0, "model:Yellow Hug")

    async def test_expired_ask_falls_back_to_dataset(self, settings):
        market = MarketAPI(settings)
        market._trait_prices["Plush Pepe"] = TraitPrices(
            models={"Yellow Hug": 25500.0}, fetched_at=time.monotonic()
        )
        market._model_asks["Plush Pepe"] = {
            "Yellow Hug": {
                "floor": 7500.0,
                "ts": time.time() - settings.MODEL_ASK_TTL_SEC - 1,
            }
        }
        assert market._observed_model_floor("Plush Pepe", "Yellow Hug") is None
