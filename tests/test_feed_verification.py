"""Тесты чистоты ленты: аукционы TonAPI не попадают в парсер,
устаревшие Portals-сделки удаляются перепроверкой, а живые лоты с
уехавшей ценой ПЕРЕСЧИТЫВАЮТ карточку под реальную цену маркета."""
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
            "sale": {"type": "AuctionSale", "price": {"value": 150_000_000_000},
                     "market": {"name": "Getgems Sales"}},
            "metadata": {"name": "Ion Gem #101"},
        },
        {   # обычная продажа
            "address": "0:bbb",
            "sale": {"type": "Sale", "price": {"value": 50_000_000_000},
                     "market": {"name": "Getgems Sales"}},
            "metadata": {"name": "Ion Gem #102"},
        },
        {   # без type (старый формат фикстур) = считаем фиксом
            "address": "0:ccc",
            "sale": {"price": {"value": 40_000_000_000},
                     "market": {"name": "Getgems Sales"}},
            "metadata": {"name": "Ion Gem #103"},
        },
    ]
    lots = market._parse_tonapi_items(items, "Ion Gem", {})
    assert [lot.price for lot in lots] == [50.0, 40.0]


async def _insert_signal(
    db,
    url: str,
    deal_price: float,
    *,
    source: str = "portals",
    floor_basis: str | None = None,
    gift: str = "Sharp Tongue",
    floor: float = 53.59,
    item_address: str | None = None,
) -> None:
    async with db.session() as session:
        session.add(
            SnipeSignal(
                gift_name=gift,
                lot_number="#6344",
                floor_price=floor,
                deal_price=deal_price,
                discount_pct=3.0,
                net_profit_ton=0.44,
                marketplace_url=url,
                source=source,
                model="Sweet Berry",
                floor_basis=floor_basis,
                item_address=item_address,
            )
        )


async def _no_floors(monkeypatch, market: MarketAPI) -> None:
    """Флоров нет — сверка с текущим рынком ничего не удаляет."""

    async def fake_floors():
        return {}

    monkeypatch.setattr(market, "get_floors", fake_floors)


async def test_verify_feed_removes_stale_deals(tmp_path, db, monkeypatch):
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)
    await _no_floors(monkeypatch, market)

    for uuid in (U1, U2, U3):
        await _insert_signal(db, f"https://t.me/portals_market_bot/market?startapp=gift_{uuid}", 52.0)

    async def fake_fetch(item_id: str):
        return {
            U1: {"status": "listed", "price": "52.2"},   # живая, цена чуть уехала — карточка обновляется
            U2: {"status": "listed", "price": "150"},    # ушла в аукцион/подорожала за допуск
            U3: {"status": "sold"},                      # продан/снят
        }[item_id]

    monkeypatch.setattr(market, "fetch_portals_item", fake_fetch)
    removed = await engine.verify_feed()
    assert removed == 2

    from sqlalchemy import select

    async with db.session() as session:
        left = list((await session.execute(select(SnipeSignal))).scalars())
    assert len(left) == 1
    # Карточка пересчитана под живую цену: 52.0 -> 52.2, дисконт и профит
    # от флора 53.59 (Portals-комиссия 2%): 2.6%, 0.24 TON.
    assert left[0].deal_price == 52.2
    assert left[0].discount_pct == 2.6
    assert left[0].net_profit_ton == 0.24


async def test_verify_feed_updates_card_on_price_drop(tmp_path, db, monkeypatch):
    """Продавец срезал цену за пределы допуска — сделка ЛУЧШЕ, карточку
    обновляем, а не удаляем: показывать 46, когда на маркете 45 (а завтра
    будет минус 100 TON), нельзя и прятать удачей тоже."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)
    await _no_floors(monkeypatch, market)

    await _insert_signal(db, f"https://t.me/portals_market_bot/market?startapp=gift_{U1}", 52.0)

    async def cheap(item_id: str):
        return {"status": "listed", "price": "40.0"}  # -23%: далеко за допуском

    monkeypatch.setattr(market, "fetch_portals_item", cheap)
    assert await engine.verify_feed() == 0  # ничего не удалено

    from sqlalchemy import select

    async with db.session() as session:
        left = list((await session.execute(select(SnipeSignal))).scalars())
    assert len(left) == 1
    assert left[0].deal_price == 40.0
    assert left[0].discount_pct == 25.4
    assert left[0].net_profit_ton == 12.44  # 53.59*0.98 - 40 - 0.08


async def test_verify_feed_updates_floor_to_current_market(tmp_path, db, monkeypatch):
    """Флор уехал вслед за рынком — зачёркнутый эталон и дисконт на карточке
    обновляются (бот показывал 46, а на маркете флор уже 45)."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fresh_floors():
        return {
            "Mad Pumpkin": {
                "market_floors": {"getgems": 14.0, "portals": 13.99},
                "floor": 13.6,
            },
        }

    monkeypatch.setattr(market, "get_floors", fresh_floors)

    # сделка ниже нового флора — остаётся, эталон 15 -> 14, дисконт от 14
    await _insert_signal(
        db, "https://getgems.io/nft/0:aaa", 12.0,
        source="getgems", floor_basis="collection", gift="Mad Pumpkin", floor=15.0,
    )
    # флор не менялся — карточка не трогается
    await _insert_signal(
        db, "https://getgems.io/nft/0:bbb", 12.0,
        source="getgems", floor_basis="collection", gift="Mad Pumpkin", floor=14.0,
    )
    assert await engine.verify_feed() == 0

    from sqlalchemy import select

    async with db.session() as session:
        left = {
            s.marketplace_url: s
            for s in (await session.execute(select(SnipeSignal))).scalars()
        }
    refreshed = left["https://getgems.io/nft/0:aaa"]
    assert refreshed.floor_price == 14.0
    assert refreshed.discount_pct == 14.3  # (14 - 12) / 14
    assert refreshed.net_profit_ton == 1.22  # 14*0.95 - 12 - 0.08
    untouched = left["https://getgems.io/nft/0:bbb"]
    assert untouched.floor_price == 14.0
    assert untouched.discount_pct == 3.0  # из фикстуры, не пересчитывался


async def test_verify_feed_keeps_rows_on_network_failure(tmp_path, db, monkeypatch):
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)
    await _no_floors(monkeypatch, market)

    await _insert_signal(db, f"https://t.me/portals_market_bot/market?startapp=gift_{U1}", 52.0)

    async def broken(item_id: str):
        return None  # API недоступен

    monkeypatch.setattr(market, "fetch_portals_item", broken)
    assert await engine.verify_feed() == 0

    from sqlalchemy import func, select

    async with db.session() as session:
        count = (await session.execute(select(func.count(SnipeSignal.id)))).scalar_one()
    assert count == 1  # наугад ничего не удаляем


async def test_verify_feed_removes_deal_above_current_floor(tmp_path, db, monkeypatch):
    """Рынок уехал вниз: сделка, пойманная ниже старого флора, выше нового — удаляется."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fallen_floors():
        return {
            "Mad Pumpkin": {
                "market_floors": {"getgems": 14.0, "portals": 13.99},
                "floor": 13.6,
            },
        }

    monkeypatch.setattr(market, "get_floors", fallen_floors)

    # ловили при флоре 30, рынок упал до 14: 25 TON выше нового флора — мусор
    await _insert_signal(
        db, "https://getgems.io/nft/0:aaa", 25.0,
        source="getgems", floor_basis="collection", gift="Mad Pumpkin", floor=30.0,
    )
    # свежая сделка ниже текущего флора — остаётся
    await _insert_signal(
        db, "https://getgems.io/nft/0:bbb", 12.0,
        source="getgems", floor_basis="collection", gift="Mad Pumpkin", floor=14.0,
    )
    # модельный базис перепроверяется тем же reference_price: эталон упал
    # до 14 (увиденные аски), карточка «-45%» — фейк вроде Yellow Hug
    await _insert_signal(
        db, "https://getgems.io/nft/0:ccc", 25.0,
        source="getgems", floor_basis="model:Yellow Hug", gift="Mad Pumpkin", floor=30.0,
    )
    # по этому подарку флоров нет — вслепую модельную карточку не трогаем
    await _insert_signal(
        db, "https://getgems.io/nft/0:ddd", 7500.0,
        source="getgems", floor_basis="model:Yellow Hug", gift="Plush Pepe", floor=25498.98,
    )

    removed = await engine.verify_feed()
    assert removed == 2

    from sqlalchemy import select

    async with db.session() as session:
        left = list((await session.execute(select(SnipeSignal))).scalars())
    assert sorted(s.deal_price for s in left) == [12.0, 7500.0]


async def test_verify_feed_keeps_deal_when_floors_unavailable(tmp_path, db, monkeypatch):
    """Флоры не получились — сверка с рынком молчит, вслепую не удаляем."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def broken_floors():
        raise RuntimeError("floors api down")

    monkeypatch.setattr(market, "get_floors", broken_floors)

    await _insert_signal(
        db, "https://getgems.io/nft/0:aaa", 25.0,
        source="getgems", floor_basis="collection", gift="Mad Pumpkin", floor=30.0,
    )
    assert await engine.verify_feed() == 0

    from sqlalchemy import func, select

    async with db.session() as session:
        count = (await session.execute(select(func.count(SnipeSignal.id)))).scalar_one()
    assert count == 1


async def test_verify_feed_removes_sold_getgems_deal(tmp_path, db, monkeypatch):
    """Getgems-лот куплен/снят (sale-объект исчез) — карточка удаляется."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fake_item(addr: str):
        # TonAPI: sale-объекта нет = лота нет на продаже
        return {} if addr == "EQ sold" else None

    monkeypatch.setattr(market, "fetch_tonapi_item", fake_item)
    await _insert_signal(
        db, "https://getgems.io/nft/EQsold", 52.0,
        source="getgems", item_address="EQ sold",
    )
    assert await engine.verify_feed() == 1

    from sqlalchemy import select

    async with db.session() as session:
        left = list((await session.execute(select(SnipeSignal))).scalars())
    assert left == []


async def test_verify_feed_removes_getgems_auction(tmp_path, db, monkeypatch):
    """Лот конвертирован в аукцион — цена в sale это ставка, не сделка."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fake_item(addr: str):
        return {"sale": {"type": "AuctionSale", "price": {"value": 52_000_000_000}}}

    monkeypatch.setattr(market, "fetch_tonapi_item", fake_item)
    await _insert_signal(
        db, "https://getgems.io/nft/EQa", 52.0,
        source="getgems", item_address="EQa",
    )
    assert await engine.verify_feed() == 1


async def test_verify_feed_removes_getgems_price_drift(tmp_path, db, monkeypatch):
    """Продавец поднял цену за допуск — карточка со старой ценой врёт."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fake_item(addr: str):
        return {"sale": {"type": "Sale", "price": {"value": 150_000_000_000}}}

    monkeypatch.setattr(market, "fetch_tonapi_item", fake_item)
    await _insert_signal(
        db, "https://getgems.io/nft/EQb", 52.0,
        source="getgems", item_address="EQb",
    )
    assert await engine.verify_feed() == 1


async def test_verify_feed_keeps_live_getgems_deal(tmp_path, db, monkeypatch):
    """Лот стоит на продаже по той же цене — карточка остаётся."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def fake_item(addr: str):
        return {"sale": {"type": "Sale", "price": {"value": 52_200_000_000}}}

    monkeypatch.setattr(market, "fetch_tonapi_item", fake_item)
    await _insert_signal(
        db, "https://getgems.io/nft/EQc", 52.0,
        source="getgems", item_address="EQc",
    )
    # без item_address проверить нечего — тоже остаётся
    await _insert_signal(
        db, "https://getgems.io/nft/EQd", 52.0,
        source="getgems", item_address=None,
    )
    assert await engine.verify_feed() == 0

    from sqlalchemy import func, select

    async with db.session() as session:
        count = (await session.execute(select(func.count(SnipeSignal.id)))).scalar_one()
    assert count == 2


async def test_verify_feed_keeps_getgems_when_fetch_fails(tmp_path, db, monkeypatch):
    """TonAPI недоступен — вслепую getgems-карточки не удаляем."""
    settings = _mk_settings(tmp_path)
    market = MarketAPI(settings)
    engine = SniperEngine(market, db, Notifier(settings), settings)

    async def broken(addr: str):
        return None

    monkeypatch.setattr(market, "fetch_tonapi_item", broken)
    await _insert_signal(
        db, "https://getgems.io/nft/EQe", 52.0,
        source="getgems", item_address="EQe",
    )
    assert await engine.verify_feed() == 0
