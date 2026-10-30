"""Тесты реестра адресов коллекций и обхода коллекций сканером."""
from __future__ import annotations

from bot.services.market_api import MarketAPI, name_variants

ADDR = lambda byte: "0:" + byte * 32  # noqa: E731


REGISTRY = [
    {"address": ADDR("11"), "name": "Plush Pepes"},
    {"address": ADDR("22"), "name": "Genie Lamps"},
    {"address": ADDR("33"), "name": "Mousse Cakes"},
    {"address": ADDR("44"), "name": "Some Wallet"},
    {"address": "EQnotarawaddress", "name": "Ion Gems"},
]


class _StubResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _StubClient:
    """Двойник httpx.AsyncClient: всегда отвечает заданным статусом."""

    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {"nft_items": []}
        self.calls = 0

    async def get(self, url: str, params: dict | None = None) -> _StubResponse:
        self.calls += 1
        return _StubResponse(self.status_code, self.payload)


def test_name_variants_join_singular_and_plural():
    assert name_variants("Plush Pepe") & name_variants("Plush Pepes")
    assert name_variants("Ion Gem") & name_variants("Ion Gems")
    assert name_variants("Durov's Cap") & name_variants("Durov's Caps")
    assert not (name_variants("Ion Gem") & name_variants("Plush Pepes"))


class TestRegistryMatching:
    def test_registry_names_are_matched_to_gift_names(self, settings):
        market = MarketAPI(settings)
        found = market._match_registry(REGISTRY)
        assert found["Plush Pepe"]["address"] == ADDR("11")
        assert found["Genie Lamp"]["address"] == ADDR("22")
        assert found["Mousse Cake"]["address"] == ADDR("33")
        assert "Some Wallet" not in found
        # Дружеский адрес (EQ...) не принимается: сканер работает с raw 0:.
        assert "Ion Gem" not in found

    def test_addresses_are_applied_to_catalog(self, settings):
        market = MarketAPI(settings)
        market._catalog = {
            "Plush Pepe": {"address": None, "address_verified": False},
            "Genie Lamp": {"address": None, "address_verified": False},
            "Kissed Frog": {"address": None, "address_verified": False},
        }
        market._collection_index = market._match_registry(REGISTRY)
        market._apply_collection_addresses()
        assert market._catalog["Plush Pepe"]["address"] == ADDR("11")
        assert market._catalog["Plush Pepe"]["address_verified"] is True
        assert market._catalog["Kissed Frog"]["address"] is None


class TestCollectionRotation:
    def test_cursor_rotates_through_collections(self, settings):
        settings.TONAPI_COLLECTIONS_PER_SCAN = 2
        market = MarketAPI(settings)
        market._catalog = {
            f"Gift {index}": {
                "address": "0:" + f"{index:064x}",
                "address_verified": True,
            }
            for index in range(5)
        }
        first = market._scan_collections()
        second = market._scan_collections()
        third = market._scan_collections()
        assert len(first) == 2
        assert not ({name for name, _ in first} & {name for name, _ in second})
        covered = {name for batch in (first, second, third) for name, _ in batch}
        assert covered == set(market._catalog)

    def test_collections_without_address_are_skipped(self, settings):
        market = MarketAPI(settings)
        market._catalog = {
            "With Address": {"address": ADDR("aa"), "address_verified": True},
            "No Address": {"address": None, "address_verified": False},
        }
        assert [name for name, _ in market._scan_collections()] == ["With Address"]

    def test_cheapest_collections_are_scanned_first(self, settings):
        """Ширпотреб в приоритете: Pepe за 6470 TON не первый в очереди."""
        settings.TONAPI_COLLECTIONS_PER_SCAN = 2
        market = MarketAPI(settings)
        market._catalog = {
            "Expensive": {"address": ADDR("01"), "address_verified": True, "floor": 6470.0},
            "Cheap": {"address": ADDR("02"), "address_verified": True, "floor": 5.0},
            "Mid": {"address": ADDR("03"), "address_verified": True, "floor": 80.0},
        }
        assert market._scan_collections() == [("Cheap", ADDR("02")), ("Mid", ADDR("03"))]


class TestPortals:
    """Публичный API Portals: парсинг листингов и флор."""

    ITEMS = [
        {
            "id": "01a16a4e-5314-77d5-9f5c-01e219b7f0bc",
            "name": "Plush Pepe",
            "external_collection_number": 14258,
            "price": "45.89",
            "status": "listed",
            "photo_url": "https://nft.fragment.com/gift/plushpepe-14258.large.jpg",
            "attributes": [
                {"type": "model", "value": "Barcelona"},
                {"type": "backdrop", "value": "Lemongrass"},
                {"type": "symbol", "value": "Cap"},
            ],
        },
        {
            "id": "01a16a4e-5314-77d5-9f5c-01e219b7f0bd",
            "name": "Plush Pepe",
            "external_collection_number": 777,
            "price": "99.0",
            "status": "sold",
        },
        {
            "id": "",
            "name": "Unknown Gift",
            "price": "5",
            "status": "listed",
        },
    ]

    def test_listed_items_become_lots_with_traits(self, settings):
        market = MarketAPI(settings)
        lots = market._parse_portals_items(self.ITEMS)
        assert len(lots) == 1
        lot = lots[0]
        assert lot.gift_name == "Plush Pepe"
        assert lot.price == 45.89
        assert lot.lot_number == "#14258"
        assert lot.source == "portals"
        assert lot.model == "Barcelona"
        assert lot.backdrop == "Lemongrass"
        assert lot.symbol == "Cap"
        assert lot.marketplace_url == (
            "https://t.me/portals_market_bot/market?startapp=gift_01a16a4e-5314-77d5-9f5c-01e219b7f0bc"
        )
        assert market.portal_model_image("Plush Pepe", "Barcelona") == (
            "https://nft.fragment.com/gift/plushpepe-14258.large.jpg"
        )

    def test_slugs_are_mapped_to_gift_names(self, settings):
        market = MarketAPI(settings)
        assert market._gift_by_slug("plushpepe") == "Plush Pepe"
        assert market._gift_by_slug("plushpepes") == "Plush Pepe"
        assert market._gift_by_slug("unknownslug") is None

    async def test_portals_floor_lands_in_market_floors(self, settings):
        market = MarketAPI(settings)
        market._portals_floors = {"Plush Pepe": 6470.0}
        market._portals_floors_at = 10**9  # «свежий» кэш
        floors = {"Plush Pepe": {"floor": 100.0, "market_floors": {"getgems": 90.0}}}
        await market._merge_portals_floors(floors)
        assert floors["Plush Pepe"]["market_floors"]["portals"] == 6470.0


class TestTonapiQuota:
    async def test_429_pauses_scans_without_extra_requests(self, settings):
        settings.TONAPI_MIN_REQUEST_INTERVAL_SEC = 0
        market = MarketAPI(settings)
        client = _StubClient(429)

        assert await market._fetch_tonapi_page(client, ADDR("ab"), 0) is None
        assert market.tonapi_cooldown_left > 0

        calls_before = client.calls
        assert await market._fetch_tonapi_page(client, ADDR("ab"), 0) is None
        assert client.calls == calls_before, "в паузе после 429 запросов быть не должно"

    async def test_successful_page_resets_nothing_and_parses_items(self, settings):
        settings.TONAPI_MIN_REQUEST_INTERVAL_SEC = 0
        market = MarketAPI(settings)
        client = _StubClient(200, {"nft_items": [{"address": "0:x"}]})
        items = await market._fetch_tonapi_page(client, ADDR("ab"), 0)
        assert items == [{"address": "0:x"}]
        assert market.tonapi_cooldown_left == 0


class TestListingsAcrossCollections:
    async def test_listings_are_requested_from_several_collections(self, settings):
        settings.TONAPI_COLLECTIONS_PER_SCAN = 3
        settings.TONAPI_SCAN_PAGES = 1
        settings.TONAPI_MIN_REQUEST_INTERVAL_SEC = 0
        market = MarketAPI(settings)
        market._catalog = {
            "Plush Pepe": {
                "address": ADDR("01"), "address_verified": True,
                "fragment_slug": "plushpepe", "image": "",
            },
            "Genie Lamp": {
                "address": ADDR("02"), "address_verified": True,
                "fragment_slug": "genielamp", "image": "",
            },
            "Mousse Cake": {
                "address": ADDR("03"), "address_verified": True,
                "fragment_slug": "moussecake", "image": "",
            },
        }
        requested: list[str] = []

        async def fake_page(client, address, offset):
            requested.append(address)
            return [{
                "address": "0:nft" + address[-2:],
                "metadata": {"name": "Gift #7"},
                "sale": {"price": {"value": "10000000000"}},
            }]

        market._fetch_tonapi_page = fake_page  # type: ignore[assignment]
        lots = await market._fetch_tonapi_listings()

        assert len(requested) == 3
        assert len(lots) == 3
        assert {lot.price for lot in lots} == {10.0}
