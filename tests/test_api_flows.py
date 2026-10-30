"""Сквозные тесты REST API через TestClient: авторизация, NFT-медиа, лимиты."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from bot.database import Database, get_or_create_user
from bot.models import SnipeSignal
from tests.conftest import make_settings, sign_init_data

USER = {"id": 777001, "first_name": "Api", "username": "api_tester"}


@pytest.fixture()
def client(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:  # вход/выход запускает lifespan (создает БД)
        yield test_client


@pytest.fixture()
def auth(client) -> dict:
    """Заголовок с валидной подписью initData для настроек этого приложения."""
    token = client.app.state.settings.BOT_TOKEN
    return {"X-Init-Data": sign_init_data(token, USER)}


class TestHealthAndStatic:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_static_index_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"GIFTTRACKER" in resp.content


class TestAuthGate:
    def test_missing_header_is_401_not_422(self, client):
        assert client.get("/api/gifts/floors").status_code == 401

    def test_garbage_header_is_401(self, client):
        resp = client.get("/api/gifts/floors", headers={"X-Init-Data": "garbage=1"})
        assert resp.status_code == 401


class TestFloorsWithNftMedia:
    def test_floors_contain_real_assets(self, client, auth):
        resp = client.get("/api/gifts/floors", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 8
        for item in body:
            assert item["image"].startswith("https://nft.fragment.com/"), item["name"]
            assert item["getgems_url"].startswith("https://getgems.io/")
            assert item["floor"] > 0
        names = {item["name"] for item in body}
        assert "Plush Pepe" in names and "Perfume Bottle" in names
        # Никаких выдуманных «коллекций»: только реальный ассет или имя подарка.
        for item in body:
            assert item["asset"] in {item["name"], "Plush Pepes", "Ion Gems"}
            assert "collection" not in item


class TestDemoFeed:
    def _seed(self, settings, **overrides) -> None:
        payload = {
            "gift_name": "Plush Pepe", "lot_number": "#9001",
            "floor_price": 25500.0, "deal_price": 17500.0, "discount_pct": 31.4,
            "net_profit_ton": 6724.92, "marketplace_url": "https://getgems.io/nft/0:deal",
            "image_url": "https://nft.fragment.com/gift/plushpepe-1668.webp",
            "item_address": "0:deal", "model": "Leonardo",
            "backdrop": "Light Olive", "symbol": "Firebird",
            "floor_basis": "model:Leonardo",
        }
        payload.update(overrides)

        async def seed() -> None:
            db = Database(settings)
            async with db.session() as session:
                session.add(SnipeSignal(**payload))
            await db.dispose()

        asyncio.run(seed())

    def test_public_floors_available_without_auth(self, client):
        resp = client.get("/api/demo/floors")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 8
        assert all(item["floor"] > 0 for item in body)

    def test_only_real_deals_are_served(self, client):
        """Публичная лента = реальные сделки; наценки над флором не отдаются."""
        settings = client.app.state.settings
        self._seed(settings)
        self._seed(
            settings,
            lot_number="#9002", deal_price=50000.0, discount_pct=-600.3,
            net_profit_ton=-4750.0, marketplace_url="https://getgems.io/nft/0:above",
            item_address="0:above", model="Bavaria", floor_basis="collection",
        )

        signals = client.get("/api/demo/feed", params={"count": 40}).json()
        assert [sig["lot_number"] for sig in signals] == ["#9001"]
        sig = signals[0]
        assert sig["model"] == "Leonardo"
        assert sig["backdrop"] == "Light Olive"
        assert sig["symbol"] == "Firebird"
        assert sig["floor_basis"] == "model:Leonardo"
        expected = round(sig["floor_price"] * 0.95 - sig["deal_price"] - 0.08, 2)
        assert abs(sig["net_profit_ton"] - expected) < 0.02

    def test_filters_by_collection_model_and_price(self, client):
        """Поиск по коллекции/модели и диапазон цен считаются в SQL."""
        settings = client.app.state.settings
        self._seed(settings)
        self._seed(
            settings,
            gift_name="Ion Gem", lot_number="#7001", floor_price=100.0,
            deal_price=80.0, discount_pct=20.0, net_profit_ton=14.92,
            marketplace_url="https://getgems.io/nft/0:ion", item_address="0:ion",
            model="Garnet", backdrop="Neon Blue", symbol="Sumerian Bird",
            floor_basis="model:Garnet",
        )

        def lots(**params):
            return [
                sig["lot_number"]
                for sig in client.get("/api/demo/feed", params=params).json()
            ]

        assert lots(q="Ion") == ["#7001"]
        assert lots(q="Leonardo") == ["#9001"]
        assert lots(q="garnet") == ["#7001"]          # регистр не важен
        assert lots(min_price=1, max_price=200) == ["#7001"]
        assert lots(min_price=10000) == ["#9001"]
        assert lots(q="Ion", min_price=100) == []
        assert lots(q="Nothing") == []

    def test_filters_work_through_authenticated_feed(self, client, auth):
        """Тот же фильтр доступен на /api/gifts/feed (initData-режим)."""
        settings = client.app.state.settings
        self._seed(settings)
        body = client.get("/api/gifts/feed", headers=auth, params={"q": "Leonardo"}).json()
        assert [sig["lot_number"] for sig in body] == ["#9001"]
        assert client.get("/api/gifts/feed", headers=auth, params={"max_price": 1}).json() == []

    def test_single_collection_cannot_flood_the_feed(self, client):
        """Лимит на коллекцию: Pepe не вытесняет дешёвые подарки из ленты."""
        settings = client.app.state.settings
        settings.FEED_MAX_PER_GIFT = 2
        for index in range(6):
            self._seed(
                settings,
                lot_number=f"#P{index}",
                marketplace_url=f"https://getgems.io/nft/0:pepe{index}",
                item_address=f"0:pepe{index}",
            )
        self._seed(
            settings,
            gift_name="Ion Gem", lot_number="#I1", floor_price=100.0,
            deal_price=80.0, discount_pct=20.0, net_profit_ton=14.92,
            marketplace_url="https://getgems.io/nft/0:ion1",
            item_address="0:ion1", model="Garnet", floor_basis="model:Garnet",
        )

        rows = client.get("/api/demo/feed", params={"count": 50}).json()

        pepe = [row for row in rows if row["gift_name"] == "Plush Pepe"]
        ion = [row for row in rows if row["gift_name"] == "Ion Gem"]
        assert len(pepe) == 2, "на коллекцию должно приходиться не больше FEED_MAX_PER_GIFT"
        assert len(ion) == 1

    def test_tiny_profit_is_not_a_deal(self, client):
        """+0.05 TON после комиссий — не сделка: минимальный профит 0.2 TON."""
        settings = client.app.state.settings
        assert settings.FEED_MIN_PROFIT_TON == 0.2
        self._seed(
            settings,
            lot_number="#tiny", floor_price=10.0, deal_price=9.4,
            discount_pct=6.0, net_profit_ton=0.05,
            marketplace_url="https://getgems.io/nft/0:tiny", item_address="0:tiny",
        )
        assert client.get("/api/demo/feed").json() == []

    def test_expensive_collections_are_hidden_by_price_cap(self, client):
        """Дефолтный потолок цены (как в UI, до 250 TON) скрывает Pepe за 17500."""
        settings = client.app.state.settings
        self._seed(settings)  # Plush Pepe, 17500 TON
        self._seed(
            settings,
            gift_name="Ion Gem", lot_number="#7001", floor_price=100.0,
            deal_price=80.0, discount_pct=20.0, net_profit_ton=14.92,
            marketplace_url="https://getgems.io/nft/0:ion", item_address="0:ion",
            model="Garnet", floor_basis="model:Garnet",
        )

        rows = client.get("/api/demo/feed", params={"max_price": 250}).json()

        assert [row["gift_name"] for row in rows] == ["Ion Gem"]


class TestModelsPicker:
    def test_models_endpoint_returns_model_floors_sorted(self, client):
        import time as time_module

        from bot.services.market_api import TraitPrices

        market = client.app.state.market
        market._trait_prices["Plush Pepe"] = TraitPrices(
            models={"Leonardo": 25500.0, "Hue Jester": 5304.0, "Red Pepple": 9100.0},
            fetched_at=time_module.monotonic(),
        )

        body = client.get("/api/demo/models", params={"name": "Plush Pepe"}).json()

        assert body["name"] == "Plush Pepe"
        assert [model["name"] for model in body["models"]] == [
            "Hue Jester", "Red Pepple", "Leonardo"
        ]
        assert body["source"] == "dataset"

    def test_models_endpoint_without_dataset_prices(self, client):
        body = client.get("/api/demo/models", params={"name": "Banana"}).json()
        assert body["models"] == []
        assert body["source"] == "none"


class TestReferral:
    def test_me_returns_referral_link_and_invites(self, client, auth):
        body = client.get("/api/user/me", headers=auth).json()
        assert body["referral_link"] == (
            f"https://t.me/GiftTracker_App?start=ref_{USER['id']}"
        )
        assert body["invited_count"] == 0


class TestAlertsFlow:
    def test_full_crud_with_free_limit(self, client, auth):
        me = client.get("/api/user/me", headers=auth)
        assert me.status_code == 200
        assert me.json()["is_pro"] is False

        created = client.post(
            "/api/alerts",
            headers=auth,
            json={"gift_name": "Plush Pepe", "max_price": 12.0, "min_discount": 20.0},
        )
        assert created.status_code == 201
        alert_id = created.json()["id"]

        # FREE-тариф: второй активный алерт запрещен.
        second = client.post(
            "/api/alerts",
            headers=auth,
            json={"gift_name": "Heroic Helmet", "max_price": 30.0, "min_discount": 15.0},
        )
        assert second.status_code == 403

        # Неизвестный подарок не проходит валидацию.
        unknown = client.post(
            "/api/alerts", headers=auth,
            json={"gift_name": "Unicorn", "max_price": 1.0, "min_discount": 5.0},
        )
        assert unknown.status_code == 422

        listing = client.get("/api/alerts", headers=auth)
        assert listing.status_code == 200 and len(listing.json()) == 1

        deleted = client.delete(f"/api/alerts/{alert_id}", headers=auth)
        assert deleted.status_code == 200
        assert client.get("/api/alerts", headers=auth).json() == []

    def test_cannot_delete_foreign_alert(self, client, auth):
        assert client.delete("/api/alerts/999", headers=auth).status_code == 404


class TestSignalFeed:
    def test_feed_returns_persisted_signals_with_media(self, client, auth):
        """Сигнал, записанный в общую SQLite-базу, виден в ленте вместе с медиа."""
        settings = client.app.state.settings

        async def seed() -> None:
            db = Database(settings)
            async with db.session() as session:
                session.add(SnipeSignal(
                    gift_name="Plush Pepe", lot_number="#9001",
                    floor_price=16.5, deal_price=10.0, discount_pct=39.4,
                    net_profit_ton=5.6, marketplace_url="https://getgems.io/nft/0:t",
                    image_url="https://nft.fragment.com/collection/plushpepe.webp",
                    item_address="0:t",
                    model="Barcelona", backdrop="Lemongrass", symbol="Cap",
                    floor_basis="model:Barcelona",
                ))
                # Наценка над флором из старой версии снайпера — в ленту не идет.
                session.add(SnipeSignal(
                    gift_name="Plush Pepe", lot_number="#9002",
                    floor_price=7140.0, deal_price=50000.0, discount_pct=-600.3,
                    net_profit_ton=-4838.08, marketplace_url="https://getgems.io/nft/0:above",
                ))
            await db.dispose()

        asyncio.run(seed())

        resp = client.get("/api/gifts/feed", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert [s["lot_number"] for s in body] == ["#9001"]
        sig = body[0]
        assert sig["image_url"].startswith("https://nft.fragment.com/")
        assert sig["item_address"] == "0:t"
        assert sig["tonkeeper_url"] == "https://app.tonkeeper.com/nft/0:t"
        assert sig["is_hot"] is True
        assert sig["model"] == "Barcelona"
        assert sig["backdrop"] == "Lemongrass"
        assert sig["symbol"] == "Cap"
        assert sig["floor_basis"] == "model:Barcelona"


class TestExpiredPro:
    def test_expired_pro_is_downgraded_and_limited_to_one_alert(self, client, auth):
        """Истекший PRO теряет безлимит: второй алерт получает 403."""
        settings = client.app.state.settings

        async def seed() -> None:
            db = Database(settings)
            async with db.session() as session:
                user = await get_or_create_user(session, {"id": USER["id"]})
                user.is_pro = True
                user.pro_expires_at = datetime.utcnow() - timedelta(days=1)
            await db.dispose()

        asyncio.run(seed())

        first = client.post(
            "/api/alerts",
            headers=auth,
            json={"gift_name": "Plush Pepe", "max_price": 12.0, "min_discount": 20.0},
        )
        assert first.status_code == 201

        second = client.post(
            "/api/alerts",
            headers=auth,
            json={"gift_name": "Heroic Helmet", "max_price": 30.0, "min_discount": 15.0},
        )
        assert second.status_code == 403


class TestInvoiceValidation:
    def test_unknown_plan_rejected_before_telegram_call(self, client, auth):
        response = client.post(
            "/api/user/invoice", headers=auth, json={"plan": "pro_week"}
        )
        assert response.status_code == 422
