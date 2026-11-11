"""Тесты аукционов: краулер делит фиксы и аукционы, Getgems-аукцион
становится сигналом is_auction=True (и не отравляет флоры), Fragment-сетка
парсится по таймеру и ставке, истёкшие аукционы чистятся перепроверкой."""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.models import SnipeSignal
from bot.services.market_api import GIFT_PRESETS, MarketAPI, Lot
from bot.services.notifier import Notifier
from bot.services.sniper_engine import SniperEngine, build_signal
from tests.conftest import make_settings


def _mk_settings(tmp_path):
    return make_settings(tmp_path, FEED_VERIFY_INTERVAL_SEC=600)


# ---------------------------------------------------------------------------
# Краулер: разделение фиксов и аукционов по интерфейсу контракта
# ---------------------------------------------------------------------------

async def test_split_auction_sales_separates_by_interface(tmp_path):
    market = MarketAPI(_mk_settings(tmp_path))

    async def fake_bulk(client, addresses):
        return {
            "0:aaa": ["nft_auction_v1"],
            "0:bbb": ["nft_sale_getgems_v3"],
        }

    market._bulk_sale_interfaces = fake_bulk
    items = [
        {"address": "0:aaa", "sale": {"address": "0:aaa", "price": {"value": 2_000_000_000}}},
        {"address": "0:bbb", "sale": {"address": "0:bbb", "price": {"value": 5_000_000_000}}},
    ]
    fixed, auctions = await market._split_auction_sales(None, items)
    assert [it["address"] for it in fixed] == ["0:bbb"]
    assert [it["address"] for it in auctions] == ["0:aaa"]


async def test_split_auction_sales_fail_open_on_bulk_failure(tmp_path):
    """Сбой _bulk: ничего не режем — аукционы уйдут на следующем проходе."""
    market = MarketAPI(_mk_settings(tmp_path))

    async def broken_bulk(client, addresses):
        return None

    market._bulk_sale_interfaces = broken_bulk
    items = [{"address": "0:aaa", "sale": {"address": "0:aaa", "price": {"value": 2_000_000_000}}}]
    fixed, auctions = await market._split_auction_sales(None, items)
    assert len(fixed) == 1 and auctions == []


# ---------------------------------------------------------------------------
# Парсер Getgems-аукционов: is_auction, без отравления флоров
# ---------------------------------------------------------------------------

def test_getgems_auction_lot_is_marked(tmp_path):
    market = MarketAPI(_mk_settings(tmp_path))
    items = [
        {
            "address": "0:ccc",
            "metadata": {"name": "Mousse Cake #91552"},
            "sale": {"price": {"value": 2_000_000_000}, "market": {"name": "Getgems Sales"}},
        },
        {
            "address": "0:ddd",
            "metadata": {"name": "Mousse Cake #44"},
            "sale": {"price": {"value": 3_000_000_000}, "market": {"name": "Marketapp Marketplace"}},
        },
        {
            "address": "0:eee",
            "metadata": {"name": "Mousse Cake #55"},
            "sale": {"price": {"value": 4_000_000_000}},  # маркет не указан — аукцион Getgems
        },
    ]
    lots = market._parse_tonapi_auction_items(
        items, "Mousse Cake", GIFT_PRESETS["Mousse Cake"]
    )
    assert [lot.lot_number for lot in lots] == ["#91552", "#55"]
    lot = lots[0]
    assert lot.is_auction is True
    assert lot.price == 2.0
    assert lot.source == "getgems"
    assert "getgems.io/nft/" in lot.marketplace_url
    # Ставка аукциона НЕ попадает в сэмплы цен и флоры (рот-баг среза пагинации).
    assert "Mousse Cake" not in market._price_samples


def test_fixed_lots_still_ignore_auction_type(tmp_path):
    """Обратная совместимость: фикс-парсер аукционы не берёт (старый формат)."""
    market = MarketAPI(_mk_settings(tmp_path))
    items = [
        {"address": "0:aaa", "metadata": {"name": "Ion Gem #101"},
         "sale": {"type": "AuctionSale", "price": {"value": 150_000_000_000},
                  "market": {"name": "Getgems Sales"}}},
    ]
    assert market._parse_tonapi_items(items, "Ion Gem", {}) == []


# ---------------------------------------------------------------------------
# Fragment: парсер аукционной сетки по таймеру и ставке
# ---------------------------------------------------------------------------

def _fragment_item(num: str, price: str, *, with_timer: bool, iso: str | None) -> str:
    timer = (
        f'<div class="tm-timer-wrap js-timer-wrap">'
        f'<div class="tm-timer"><time datetime="{iso}" data-relative="short-text">17 hours left</time>'
        f" left</div></div>"
        if with_timer
        else ""
    )
    status = "<span class='thin-only'>Auction</span>" if with_timer else "Available"
    return (
        f'<a href="/gift/moussecake-{num}?filter=auction" class="tm-grid-item">'
        f'<img src="https://nft.fragment.com/gift/moussecake-{num}.medium.jpg" class="tm-grid-thumb"/>'
        f'<div class="tm-grid-item-num thin-only">#{num}</div>'
        f'<div class="tm-grid-item-content">'
        f'<div class="tm-grid-item-desc wide-only">{timer}</div>'
        f'<div class="tm-grid-item-values">'
        f'<div class="tm-grid-item-value tm-value icon-before icon-ton">{price}</div>'
        f'<div class="tm-grid-item-status tm-status-avail">{status}</div>'
        f"</div></div></a>"
    )


def test_fragment_auction_page_parse(tmp_path):
    market = MarketAPI(_mk_settings(tmp_path))
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=17)).isoformat()
    html = (
        _fragment_item("77789", "10", with_timer=True, iso=future_iso)
        + _fragment_item("44255", "250", with_timer=False, iso=None)  # не активный
    )
    lots = market._parse_fragment_auction_page(html, "Mousse Cake", "moussecake")
    assert len(lots) == 1
    lot = lots[0]
    assert lot.lot_number == "#77789"
    assert lot.price == 10.0
    assert lot.source == "fragment"
    assert lot.is_auction is True
    assert lot.auction_end_ts and lot.auction_end_ts > time.time()
    assert lot.marketplace_url == "https://fragment.com/gift/moussecake-77789"
    assert lot.image_url == "https://nft.fragment.com/gift/moussecake-77789.webp"


def test_fragment_expired_timer_is_skipped(tmp_path):
    market = MarketAPI(_mk_settings(tmp_path))
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    html = _fragment_item("1001", "3", with_timer=True, iso=past_iso)
    assert market._parse_fragment_auction_page(html, "Mousse Cake", "moussecake") == []


# ---------------------------------------------------------------------------
# Сигнал и чистка истёкших аукционов
# ---------------------------------------------------------------------------

def test_build_signal_carries_auction_flags(tmp_path):
    settings = _mk_settings(tmp_path)
    lot = Lot(
        gift_name="Mousse Cake",
        lot_number="#91552",
        price=2.0,
        marketplace_url="https://fragment.com/gift/moussecake-91552",
        source="fragment",
        is_auction=True,
        auction_end_ts=1750000000.0,
    )
    signal = build_signal(lot, 5.58, settings)
    assert signal is not None
    assert signal.is_auction is True
    assert signal.auction_end_ts == 1750000000.0
    payload = signal.as_dict()
    assert payload["is_auction"] is True
    assert payload["auction_end_ts"] == 1750000000.0


async def test_verify_feed_removes_expired_auction(tmp_path, db, monkeypatch):
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fake_floors():
        return {}

    async def fake_fetch(item_address):
        return None  # сеть не отвечает — сетевой путь ничего не решает

    monkeypatch.setattr(market, "get_floors", fake_floors)
    monkeypatch.setattr(market, "fetch_tonapi_item", fake_fetch)

    async with db.session() as session:
        session.add(
            SnipeSignal(
                gift_name="Mousse Cake", lot_number="#1", floor_price=5.58,
                deal_price=2.0, discount_pct=64.0, net_profit_ton=3.0,
                marketplace_url="https://fragment.com/gift/moussecake-1",
                source="fragment", is_auction=True,
                auction_end_ts=time.time() - 60,  # истёк — удаляется без сети
            )
        )
        session.add(
            SnipeSignal(
                gift_name="Mousse Cake", lot_number="#2", floor_price=5.58,
                deal_price=2.5, discount_pct=55.0, net_profit_ton=2.7,
                marketplace_url="https://fragment.com/gift/moussecake-2",
                source="fragment", is_auction=True,
                auction_end_ts=time.time() + 3600,  # ещё идёт — остаётся
            )
        )
    await engine.verify_feed()
    async with db.session() as session:
        urls = {
            row.marketplace_url
            for row in (await session.execute(select(SnipeSignal))).scalars()
        }
    assert urls == {"https://fragment.com/gift/moussecake-2"}


async def test_verify_feed_keeps_live_getgems_auction(tmp_path, db, monkeypatch):
    """Аукционная карточка НЕ удаляется по факту «это аукцион»: живая ставка
    ниже эталона — рабочая возможность, удаляет её только перебитый флор."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fake_floors():
        return {
            "Ion Gem": {
                "market_floors": {"getgems": 80.0},
                "floor": 80.0,
            }
        }

    async def fake_fetch(item_address):
        return {  # живой аукцион: TonAPI отвечает sale без type
            "sale": {"address": "0:sale1", "price": {"value": int(60.0 * 1e9)}},
        }

    async def fake_is_auction(sale_address):
        return True  # интерфейс nft_auction_v1 — но карточка сама is_auction

    monkeypatch.setattr(market, "get_floors", fake_floors)
    monkeypatch.setattr(market, "fetch_tonapi_item", fake_fetch)
    monkeypatch.setattr(market, "is_auction_sale", fake_is_auction)

    async with db.session() as session:
        session.add(
            SnipeSignal(
                gift_name="Ion Gem", lot_number="#7", floor_price=80.0,
                deal_price=50.0, discount_pct=37.5, net_profit_ton=25.0,
                marketplace_url="https://getgems.io/nft/EQTest", source="getgems",
                item_address="EQTest", is_auction=True,
            )
        )
    await engine.verify_feed()
    async with db.session() as session:
        rows = (await session.execute(select(SnipeSignal))).scalars().all()
    assert len(rows) == 1
    # Ставка выросла 50 -> 60: карточка обновилась под живую ставку.
    assert rows[0].deal_price == 60.0
