"""Тесты чистоты ленты: аукционы TonAPI не попадают в парсер,
устаревшие Portals-сделки удаляются перепроверкой."""
from __future__ import annotations

import pytest

from bot.models import SnipeSignal
from bot.services.market_api import MarketAPI
from bot.services.notifier import Notifier
from bot.services.sniper_engine import SniperEngine
from tests.conftest import make_settings

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"


def _mk_settings(tmp_path):
    return make_settings(
        tmp_path,
        FEED_VERIFY_PRICE_TOLERANCE=0.05,
        FEED_VERIFY_INTERVAL_SEC=600,
    )


def test_tonapi_auction_not_parsed(tmp_path):
    """AuctionSale с «текущей ставкой» не должен выглядеть как сделка-фикс."""
    market = MarketAPI(_mk_settings(tmp_path))
    items = [
        {   # аукцион: price = текущая ставка 150 TON — в ленту НЕ попадает
            "address": "0:aaa",
            "sale": {"type": "AuctionSale", "price": {"value": 150_000_000_000}},
            "metadata": {"name": "Ion Gem #101"},
        },
        {   # обычная продажа
            "address": "0:bbb",
            "sale": {"type": "Sale", "price": {"value": 50_000_000_000}},
            "metadata": {"name": "Ion Gem #102"},
        },
        {   # без type (старый формат фикстур) = считаем фиксом
            "address": "0:ccc",
            "sale": {"price": {"value": 40_000_000_000}},
            "metadata": {"name": "Ion Gem #103"},
        },
    ]
    lots = market._parse_tonapi_items(items, "Ion Gem", {})
    assert [lot.price for lot in lots] == [50.0, 40.0]


async def _insert_signal(db, url: str, deal_price: float) -> None:
    async with db.session() as session:
        session.add(
            SnipeSignal(
                gift_name="Sharp Tongue",
                lot_number="#6344",
                floor_price=53.59,
                deal_price=deal_price,
                discount_pct=3.0,
                net_profit_ton=0.44,
                marketplace_url=url,
                source="portals",
                model="Sweet Berry",
            )
        )


async def test_verify_feed_removes_stale_deals(tmp_path, db, monkeypatch):
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    for uuid in (U1, U2, U3):
        await _insert_signal(db, f"https://t.me/portals_market_bot/market?startapp=gift_{uuid}", 52.0)

    async def fake_fetch(item_id: str):
        return {
            U1: {"status": "listed", "price": "52.2"},   # живая, цена в допуске
            U2: {"status": "listed", "price": "150"},    # ушла в аукцион/подорожала
            U3: {"status": "sold"},                      # продан/снят
        }[item_id]

    monkeypatch.setattr(market, "fetch_portals_item", fake_fetch)
    removed = await engine.verify_feed()
    assert removed == 2

    from sqlalchemy import select

    async with db.session() as session:
        left = list((await session.execute(select(SnipeSignal))).scalars())
    assert len(left) == 1
    assert left[0].deal_price == 52.0


async def test_verify_feed_keeps_rows_on_network_failure(tmp_path, db, monkeypatch):
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    await _insert_signal(db, f"https://t.me/portals_market_bot/market?startapp=gift_{U1}", 52.0)

    async def broken(item_id: str):
        return None  # API недоступен

    monkeypatch.setattr(market, "fetch_portals_item", broken)
    assert await engine.verify_feed() == 0

    from sqlalchemy import func, select

    async with db.session() as session:
        count = (await session.execute(select(func.count(SnipeSignal.id)))).scalar_one()
    assert count == 1  # наугад ничего не удаляем
