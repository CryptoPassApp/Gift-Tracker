"""Сквозные тесты REST API через TestClient: авторизация, NFT-медиа, лимиты."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from bot.database import Database
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
        assert "Plush Pepe" in names and "Champagne" in names


class TestDemoFeed:
    def test_demo_feed_has_media_and_math(self, client):
        resp = client.get("/api/demo/feed", params={"count": 5})
        assert resp.status_code == 200
        signals = resp.json()
        assert len(signals) >= 1
        for sig in signals:
            assert sig["image_url"].startswith("https://nft.fragment.com/")
            assert sig["marketplace_url"].startswith("https://getgems.io/")
            expected = round(sig["floor_price"] * 0.95 - sig["deal_price"] - 0.08, 2)
            assert abs(sig["net_profit_ton"] - expected) < 0.02


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
            json={"gift_name": "Spaceman", "max_price": 30.0, "min_discount": 15.0},
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
                ))
            await db.dispose()

        asyncio.run(seed())

        resp = client.get("/api/gifts/feed", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        sig = next(s for s in body if s["lot_number"] == "#9001")
        assert sig["image_url"].startswith("https://nft.fragment.com/")
        assert sig["item_address"] == "0:t"
        assert sig["tonkeeper_url"] == "https://app.tonkeeper.com/nft/0:t"
        assert sig["is_hot"] is True
