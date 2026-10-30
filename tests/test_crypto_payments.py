"""Тесты крипто-оплаты Crypto Pay: инвойсы, синхронизация, идемпотентность.

Crypto Pay API не вызывается по сети — httpx.MockTransport подменяет ответы
(тот же принцип, что в остальном сьюте: pytest всегда офлайн).
"""
from __future__ import annotations

import httpx
import pytest

from bot.database import get_or_create_user
from bot.keyboards import pro_plans_kb
from bot.services.crypto_payments import CryptoPayClient, CryptoPaymentService
from tests.conftest import make_settings

USER_TG_ID = 424_242

CREATE_INVOICE_RESULT = {
    "invoice_id": 777_001,
    "status": "active",
    "amount": "3",
    "asset": "USDT",
    "bot_invoice_url": "https://t.me/CryptoBot?startapp=r_777001",
    "mini_app_invoice_url": "https://t.me/CryptoBot/startapp?invoice=777001",
    "web_app_invoice_url": "https://wallet.crypto.bot/invoice/777001",
}

PAID_INVOICE_RESULT = {
    "items": [
        {
            "invoice_id": 777_001,
            "status": "paid",
            "amount": "3",
            "asset": "USDT",
        }
    ]
}


def _crypto_settings(tmp_path, **overrides):
    defaults = {
        "CRYPTO_PAY_TOKEN": "381818:TEST:pay-token",
        "CRYPTO_MONTH_USD": 3.0,
        "CRYPTO_LIFETIME_USD": 20.0,
    }
    defaults.update(overrides)
    return make_settings(tmp_path, **defaults)


def _client_with_routes(invoices_by_call: dict[str, object]) -> CryptoPayClient:
    """CryptoPayClient с MockTransport: createInvoice/getInvoices без сети."""

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", maxsplit=1)[-1]
        payload = invoices_by_call.get(method)
        if payload is None:
            return httpx.Response(200, json={"ok": False, "error": "unexpected method"})
        return httpx.Response(200, json={"ok": True, "result": payload})

    return CryptoPayClient(
        token="381818:TEST:pay-token",
        api_base="https://pay.crypt.bot/api",
        transport=httpx.MockTransport(handler),
    )


def _service(settings, db, client: CryptoPayClient) -> CryptoPaymentService:
    return CryptoPaymentService(db, settings, bot=None, client=client)


async def _make_user(db):
    async with db.session() as session:
        user = await get_or_create_user(session, {"id": USER_TG_ID, "username": "crypto_tester"})
        await session.flush()
        return user.id


async def test_price_for_plans(tmp_path, db, settings):
    settings = _crypto_settings(tmp_path)
    service = _service(settings, db, _client_with_routes({}))
    assert service.price_for("pro_month") == 3.0
    assert service.price_for("pro_lifetime") == 20.0
    with pytest.raises(ValueError):
        service.price_for("pro_week")


async def test_create_for_user_stores_invoice(tmp_path, db, settings):
    settings = _crypto_settings(tmp_path)
    service = _service(settings, db, _client_with_routes({"createInvoice": CREATE_INVOICE_RESULT}))
    user_id = await _make_user(db)

    class _User:  # достаточно id для сервиса
        pass

    holder = _User()
    holder.id = user_id
    holder.telegram_id = USER_TG_ID
    invoice_id, url = await service.create_for_user(holder, "pro_month")

    assert invoice_id == 777_001
    # Приоритет ссылки: mini_app_invoice_url.
    assert url == CREATE_INVOICE_RESULT["mini_app_invoice_url"]

    stored = await db.get_crypto_invoice(invoice_id)
    assert stored is not None
    assert stored.user_id == user_id
    assert stored.plan == "pro_month"
    assert stored.status == "active"
    assert float(stored.amount) == 3.0


async def test_sync_once_activates_pro_once(tmp_path, db, settings):
    settings = _crypto_settings(tmp_path)
    service = _service(
        settings,
        db,
        _client_with_routes({"createInvoice": CREATE_INVOICE_RESULT, "getInvoices": PAID_INVOICE_RESULT}),
    )
    user_id = await _make_user(db)

    class _User:
        pass

    holder = _User()
    holder.id = user_id
    holder.telegram_id = USER_TG_ID
    invoice_id, _url = await service.create_for_user(holder, "pro_month")

    activated = await service.sync_once()
    assert activated == 1

    user_row = await db.user_by_id(user_id)
    assert user_row is not None and user_row.is_pro is True

    stored = await db.get_crypto_invoice(invoice_id)
    assert stored is not None and stored.status == "paid"

    # Повторный проход: инвойс уже paid -> никаких повторных активаций.
    assert await service.sync_once() == 0


async def test_settle_invoice_idempotent(tmp_path, db, settings):
    settings = _crypto_settings(tmp_path)
    service = _service(
        settings,
        db,
        _client_with_routes({"createInvoice": CREATE_INVOICE_RESULT, "getInvoices": PAID_INVOICE_RESULT}),
    )
    user_id = await _make_user(db)

    class _User:
        pass

    holder = _User()
    holder.id = user_id
    holder.telegram_id = USER_TG_ID
    invoice_id, _url = await service.create_for_user(holder, "pro_month")

    assert await service.settle_invoice(invoice_id) is True
    first = await db.user_by_id(user_id)
    assert first is not None and first.pro_expires_at is not None

    # Повторный settle того же инвойса не продлевает PRO второй раз.
    assert await service.settle_invoice(invoice_id) is False
    second = await db.user_by_id(user_id)
    assert second is not None and second.pro_expires_at == first.pro_expires_at


async def test_check_invoice_now_requires_paid(tmp_path, db, settings):
    settings = _crypto_settings(tmp_path)
    not_paid_client = _client_with_routes({"getInvoices": {"items": []}})
    service = _service(settings, db, not_paid_client)
    user_id = await _make_user(db)

    async with db.session() as session:
        from bot.models import CryptoPayment

        session.add(
            CryptoPayment(
                invoice_id=555,
                user_id=user_id,
                plan="pro_month",
                asset="USDT",
                amount=3.0,
                status="active",
            )
        )

    assert await service.check_invoice_now(555) is False

    paid_client = _client_with_routes({"getInvoices": PAID_INVOICE_RESULT})
    service2 = _service(settings, db, paid_client)
    assert await service2.check_invoice_now(777_001) is False  # нет в БД — не начисляем


async def test_disabled_without_token(tmp_path, db):
    settings = _crypto_settings(tmp_path, CRYPTO_PAY_TOKEN="")
    service = _service(settings, db, _client_with_routes({}))
    assert service.enabled is False
    assert await service.sync_once() == 0


def test_keyboard_crypto_rows():
    without = pro_plans_kb()
    texts = [btn.text for row in without.inline_keyboard for btn in row]
    assert not any("криптой" in text.lower() for text in texts)

    with_crypto = pro_plans_kb({"pro_month": 3.0, "pro_lifetime": 20.0})
    texts = [btn.text for row in with_crypto.inline_keyboard for btn in row]
    assert any("3" in text and "криптой" in text.lower() for text in texts)
    assert any("20" in text and "криптой" in text.lower() for text in texts)
