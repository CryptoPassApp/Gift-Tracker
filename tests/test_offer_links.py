"""Тесты ссылок на офферы всех 3 платформ (Getgems / Portals / MRKT)."""
from __future__ import annotations

from datetime import datetime

from api.routes.gifts import _signal_to_dict, floors_payload
from bot.models import SnipeSignal
from bot.services.market_api import (
    MRKT_MINIAPP_URL,
    PORTALS_ITEM_URL,
    PORTALS_MARKET_URL,
    TONNEL_MARKET_URL,
    Lot,
    MarketAPI,
    friendly_address,
    getgems_collection_url,
    getgems_item_url,
    mrkt_lot_url,
    tonnel_lot_url,
)


class FakeMarket:
    def __init__(self, floors: dict) -> None:
        self._floors = floors

    async def get_floors(self) -> dict:
        return self._floors

    def get_catalog(self) -> dict:
        return {}


class TestMarketplaceUrls:
    def test_portals_market_url_is_official_mini_app(self):
        assert PORTALS_MARKET_URL == "https://t.me/portals/market?startapp=to_market"

    def test_mrkt_url_is_official_mini_app(self):
        assert MRKT_MINIAPP_URL == "https://t.me/mrkt/app"

    def test_tonnel_market_url_is_official_mini_app(self):
        assert TONNEL_MARKET_URL == "https://t.me/tonnel_network_bot/gift"

    def test_tonnel_lot_url(self):
        assert tonnel_lot_url("12345") == "https://t.me/tonnel_network_bot/gift?startapp=12345"
        assert tonnel_lot_url() == TONNEL_MARKET_URL
        assert tonnel_lot_url("") == TONNEL_MARKET_URL

    def test_portals_lot_links_point_to_exact_gift(self, settings):
        market = MarketAPI(settings)
        lots = market._parse_portals_items(
            [
                {
                    "id": "abc-123",
                    "status": "listed",
                    "price": "42.5",
                    "name": "Plush Pepe",
                }
            ]
        )
        assert len(lots) == 1
        # Deep-link из share-генератора самого Portals: открывает Mini App
        # сразу на этом гифте, а не на общей витрине маркета.
        assert lots[0].marketplace_url == PORTALS_ITEM_URL.format(id="abc-123")
        assert lots[0].marketplace_url == (
            "https://t.me/portals_market_bot/market?startapp=gift_abc-123"
        )

    def test_mrkt_lot_url_prefers_api_link(self):
        assert mrkt_lot_url({"url": "https://mrkt.io/gift/xyz"}) == "https://mrkt.io/gift/xyz"

    def test_mrkt_lot_url_falls_back_to_mini_app(self):
        assert mrkt_lot_url({}) == MRKT_MINIAPP_URL
        assert mrkt_lot_url({"url": "javascript:alert(1)"}) == MRKT_MINIAPP_URL

    def test_getgems_lot_keeps_item_link(self):
        lot = Lot(
            gift_name="Plush Pepe",
            lot_number="#1",
            price=100.0,
            marketplace_url="https://getgems.io/nft/0:abc",
            item_address="0:abc",
        )
        assert lot.marketplace_url == "https://getgems.io/nft/0:abc"


class TestFloorsPayloadOffers:
    async def test_floors_expose_offer_urls_for_all_3_platforms(self, db, settings):
        settings.MARKET_SOURCE = "giftdataset"
        market = FakeMarket(
            {
                "Plush Pepe": {
                    "floor": 6470.0,
                    "change_24h": None,
                    "liquidity": {"label": "HIGH", "value": "high"},
                    "image": "https://nft.fragment.com/collection/plushpepe.webp",
                    "address": "0:46fa",
                    "market_floors": {"getgems": 6470.0, "tgmrkt": 6400.0, "portals": 6450.0},
                }
            }
        )
        payload = await floors_payload(market, settings)
        entry = payload[0]
        # У Plush Pepe есть проверенный адрес пресета -> страница коллекции.
        assert entry["offer_urls"]["getgems"].startswith("https://getgems.io/collection/EQ")
        assert entry["offer_urls"]["portals"] == PORTALS_MARKET_URL
        assert entry["offer_urls"]["mrkt"] == MRKT_MINIAPP_URL
        # Tonnel убран из офферов (их API закрыт Cloudflare, лотов нет).
        assert "tonnel" not in entry["offer_urls"]
        # Живого бида MRKT у FakeMarket нет -> поле пустое, фильтр скроется.
        assert entry["mrkt_bid"] is None
        # Флоры маркетов идут рядом со ссылками, чтобы чипы подписывать ценой.
        assert entry["market_floors"] == {"getgems": 6470.0, "tgmrkt": 6400.0, "portals": 6450.0}

    async def test_floors_without_market_floors_still_have_links(self, db, settings):
        settings.MARKET_SOURCE = "giftdataset"
        market = FakeMarket(
            {
                "Ion Gem": {
                    "floor": 74.0,
                    "change_24h": None,
                    "liquidity": {"label": "MED", "value": "med"},
                    "image": None,
                    "address": None,
                }
            }
        )
        payload = await floors_payload(market, settings)
        entry = payload[0]
        # Ion Gem тоже с проверенным адресом; главное — все ссылки на месте.
        assert entry["offer_urls"]["getgems"].startswith("https://getgems.io/")
        assert entry["offer_urls"]["portals"] == PORTALS_MARKET_URL
        assert entry["offer_urls"]["mrkt"] == MRKT_MINIAPP_URL
        assert "tonnel" not in entry["offer_urls"]
        assert entry["market_floors"] == {}

    def test_fee_for_source_tonnel(self, settings):
        assert settings.fee_for_source("tonnel") == 0.025


class TestFriendlyAddressConversion:
    def test_golden_pair_conversion(self):
        raw = "0:c73e75bf0037e4944b132f61f286f938873ce823c8a1c5f28806112e358f96c6"
        expected = "EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"
        assert friendly_address(raw) == expected

    def test_plush_pepe_collection_address(self):
        raw = "0:46fa0e9a864014196a5e7d66f1f83ffdb10f2859bbf2ea9baeabbf14d9ce0d50"
        expected = "EQBG-g6ahkAUGWpefWbx-D_9sQ8oWbvy6puuq78U2c4NUDFS"
        assert friendly_address(raw) == expected

    def test_already_friendly_address_untouched(self):
        eq = "EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"
        assert friendly_address(eq) == eq

    def test_invalid_or_short_address_safe(self):
        assert friendly_address("") == ""
        assert friendly_address(None) == ""
        assert friendly_address("0:short") == "0:short"
        assert friendly_address("not_an_address") == "not_an_address"

    def test_getgems_collection_url_uses_friendly_address(self):
        # Plush Pepe имеет проверенный адрес в GIFT_PRESETS
        url = getgems_collection_url("Plush Pepe")
        assert url == "https://getgems.io/collection/EQBG-g6ahkAUGWpefWbx-D_9sQ8oWbvy6puuq78U2c4NUDFS"

    def test_getgems_item_url_uses_friendly_address(self):
        raw = "0:c73e75bf0037e4944b132f61f286f938873ce823c8a1c5f28806112e358f96c6"
        url = getgems_item_url(raw)
        assert url == "https://getgems.io/nft/EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"

    def test_parse_tonapi_items_produces_clean_eq_url_without_price(self, settings):
        market = MarketAPI(settings)
        items = [
            {
                "address": "0:c73e75bf0037e4944b132f61f286f938873ce823c8a1c5f28806112e358f96c6",
                "sale": {"price": {"value": "499000000000"}},
                "metadata": {"name": "Mighty Arm #2494"},
            }
        ]
        lots = market._parse_tonapi_items(items, "Mighty Arm", {})
        assert len(lots) == 1
        lot = lots[0]
        assert lot.marketplace_url == "https://getgems.io/nft/EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"
        assert "?price=" not in lot.marketplace_url
        assert lot.item_address == "EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"

    def test_signal_to_dict_normalizes_legacy_0_and_price_in_url(self):
        raw_sig = SnipeSignal(
            gift_name="Mighty Arm",
            lot_number="#2494",
            floor_price=600.0,
            deal_price=499.0,
            discount_pct=16.8,
            net_profit_ton=70.92,
            marketplace_url="https://getgems.io/nft/0:c73e75bf0037e4944b132f61f286f938873ce823c8a1c5f28806112e358f96c6?price=499.00",
            image_url=None,
            item_address="0:c73e75bf0037e4944b132f61f286f938873ce823c8a1c5f28806112e358f96c6",
            source="getgems",
            created_at=datetime.utcnow(),
        )
        d = _signal_to_dict(raw_sig)
        assert d["marketplace_url"] == "https://getgems.io/nft/EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"
        assert d["item_address"] == "EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"
        assert d["tonkeeper_url"] == "https://app.tonkeeper.com/nft/EQDHPnW_ADfklEsTL2Hyhvk4hzzoI8ihxfKIBhEuNY-Wxo6d"

    def test_signal_to_dict_upgrades_legacy_portals_fragment_url(self):
        raw_sig = SnipeSignal(
            gift_name="Genie Lamp",
            lot_number="#243",
            floor_price=38.0,
            deal_price=29.0,
            discount_pct=23.7,
            net_profit_ton=7.02,
            marketplace_url="https://t.me/portals/market?startapp=to_market#01a09e96-da81-719c-b33b-cf0829ce91bd",
            image_url=None,
            item_address=None,
            source="portals",
            created_at=datetime.utcnow(),
        )
        d = _signal_to_dict(raw_sig)
        assert d["marketplace_url"] == (
            "https://t.me/portals_market_bot/market?startapp=gift_"
            "01a09e96-da81-719c-b33b-cf0829ce91bd"
        )

    def test_signal_to_dict_maps_dead_portals_market_com_to_market(self):
        raw_sig = SnipeSignal(
            gift_name="Genie Lamp",
            lot_number="#243",
            floor_price=38.0,
            deal_price=29.0,
            discount_pct=23.7,
            net_profit_ton=7.02,
            marketplace_url="https://portals-market.com/nft/genielamp_243",
            image_url=None,
            item_address=None,
            source="portals",
            created_at=datetime.utcnow(),
        )
        d = _signal_to_dict(raw_sig)
        assert d["marketplace_url"] == PORTALS_MARKET_URL
