"""Тесты редактирования алертов (PATCH /api/alerts/{id})."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from tests.conftest import make_settings, sign_init_data

USER = {"id": 920202, "first_name": "Editor", "username": "alert_editor"}


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


def create_alert(client, auth, **overrides) -> dict:
    payload = {"gift_name": "Plush Pepe", "max_price": 12.0, "min_discount": 20.0}
    payload.update(overrides)
    resp = client.post("/api/alerts", headers=auth, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestAlertEditing:
    def test_patch_updates_all_fields(self, client, auth):
        alert = create_alert(client, auth)

        resp = client.patch(
            f"/api/alerts/{alert['id']}",
            headers=auth,
            json={"gift_name": "Heroic Helmet", "max_price": 30.5, "min_discount": 35},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["gift_name"] == "Heroic Helmet"
        assert body["max_price"] == 30.5
        assert body["min_discount"] == 35.0

        listing = client.get("/api/alerts", headers=auth).json()
        assert listing[0]["gift_name"] == "Heroic Helmet"

    def test_patch_single_field_keeps_others(self, client, auth):
        alert = create_alert(client, auth)
        resp = client.patch(f"/api/alerts/{alert['id']}", headers=auth, json={"max_price": 9.9})
        assert resp.status_code == 200
        body = resp.json()
        assert body["max_price"] == 9.9
        assert body["gift_name"] == "Plush Pepe"
        assert body["min_discount"] == 20.0

    def test_patch_unknown_gift_rejected(self, client, auth):
        alert = create_alert(client, auth)
        resp = client.patch(
            f"/api/alerts/{alert['id']}", headers=auth, json={"gift_name": "Unicorn"}
        )
        assert resp.status_code == 422

    def test_patch_empty_body_rejected(self, client, auth):
        alert = create_alert(client, auth)
        resp = client.patch(f"/api/alerts/{alert['id']}", headers=auth, json={})
        assert resp.status_code == 422

    def test_patch_foreign_alert_returns_404(self, client, auth):
        resp = client.patch("/api/alerts/99999", headers=auth, json={"max_price": 5.0})
        assert resp.status_code == 404

    def test_patch_requires_auth(self, client):
        resp = client.patch("/api/alerts/1", json={"max_price": 5.0})
        assert resp.status_code == 401

    def test_patch_out_of_range_rejected(self, client, auth):
        alert = create_alert(client, auth)
        for payload in ({"max_price": 0}, {"max_price": 2000000}, {"min_discount": 0}, {"min_discount": 100}):
            resp = client.patch(f"/api/alerts/{alert['id']}", headers=auth, json=payload)
            assert resp.status_code == 422, payload

    def test_alert_high_price_supported(self, client, auth):
        """Дорогие подарки (Plush Pepe Leonardo флор 25500 TON) должны поддерживаться."""
        alert = create_alert(client, auth, max_price=25500.0)
        assert alert["max_price"] == 25500.0

    def test_editing_does_not_need_pro(self, client, auth):
        """Редактирование доступно и FREE (лимит проверяется только при создании)."""
        alert = create_alert(client, auth)
        resp = client.patch(f"/api/alerts/{alert['id']}", headers=auth, json={"min_discount": 25})
        assert resp.status_code == 200
