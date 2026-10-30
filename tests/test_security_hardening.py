"""Тесты мер безопасности (Hardening): лимитер DoS, заголовки и маскирование демо."""
from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from api.rate_limiter import InMemoryRateLimiter


class TestRateLimiter:
    def test_in_memory_rate_limiter_blocks_on_excess(self):
        limiter = InMemoryRateLimiter(max_requests=3, window_sec=60)

        # Mock request from a real IP
        class MockRequest:
            client = type("Client", (), {"host": "192.168.1.50"})()
            headers = {}

        req = MockRequest()

        # 3 allowed
        limiter.check(req)
        limiter.check(req)
        limiter.check(req)

        # 4th must raise 429
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            limiter.check(req)
        assert exc.value.status_code == 429


class TestSecurityHeaders:
    def test_security_headers_present_on_endpoints(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.headers.get("referrer-policy") == "no-referrer"
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert "frame-ancestors" in resp.headers.get("content-security-policy", "")

    def test_demo_feed_masks_direct_wallet_links(self, client: TestClient):
        # Even when signals exist, demo feed must not leak tonkeeper_url or contract address
        resp = client.get("/api/demo/feed")
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert item.get("tonkeeper_url") is None
            assert item.get("item_address") is None
