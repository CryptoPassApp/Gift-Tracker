"""Rate limiter на публичных /api/demo/*: 31-й запрос за минуту -> 429."""
from __future__ import annotations


def test_demo_rate_limited(client):
    statuses = []
    for _ in range(31):
        r = client.get("/api/demo/feed")
        statuses.append(r.status_code)
    # Первые 30 проходят, 31-й за окно — 429
    assert all(s == 200 for s in statuses[:30]), statuses[:30]
    assert statuses[30] == 429


def test_health_not_rate_limited(client):
    """Лимит касается только /api/demo/* — health держит любые частоты."""
    for _ in range(35):
        r = client.get("/api/health")
        assert r.status_code == 200


def test_authed_endpoints_not_rate_limited(client):
    """/api/gifts/* под initData не лимитируется (лимит только для витрины)."""
    import pytest

    from tests.conftest import TEST_BOT_TOKEN, sign_init_data

    init = sign_init_data(TEST_BOT_TOKEN, {"id": 555001, "first_name": "Rl"})
    last = None
    for _ in range(32):
        last = client.get("/api/gifts/floors", headers={"X-Init-Data": init})
    assert last.status_code == 200
