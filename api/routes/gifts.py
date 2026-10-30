"""Эндпоинты рыночных данных: /api/gifts/floors и /api/gifts/feed."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select

from api.dependencies import get_current_user
from bot.config import get_settings
from bot.models import SnipeSignal, User
from bot.services.market_api import (
    GIFT_PRESETS,
    MRKT_MINIAPP_URL,
    PORTALS_ITEM_URL,
    PORTALS_MARKET_URL,
    TONNEL_MARKET_URL,
    fragment_model_image,
    friendly_address,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gifts", tags=["gifts"])


async def query_feed(
    db: Any,
    settings: Any,
    *,
    q: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int | None = None,
    per_gift_limit: int | None = None,
) -> list[SnipeSignal]:
    """Выборка ленты: только сделки + фильтры коллекции/модели и диапазона цен.

    Фильтры применяются в SQL, а не в браузере: иначе поиск по коллекции
    видел бы только последние N записей и терял сделки за их пределами.
    Дополнительно ограничиваем число лотов одной коллекции: без этого
    Plush Pepe (дорогие лоты, много сделок) занимает всю ленту и вытесняет
    дешёвые подарки, которыми реально пользуются покупатели.
    """
    limit = max(1, min(int(limit or settings.FEED_HISTORY_LIMIT), 200))
    cap = int(per_gift_limit if per_gift_limit is not None else settings.FEED_MAX_PER_GIFT)
    cap = max(1, min(cap, 50))
    # Читаем с запасом: часть строк отсеет лимит на коллекцию.
    fetch_limit = min(max(limit * 10, 100), 800)

    stmt = select(SnipeSignal).where(
        SnipeSignal.discount_pct > 0,
        # +0.03 TON после комиссии — не сделка: отсекаем по минимальному профиту.
        SnipeSignal.net_profit_ton >= float(settings.FEED_MIN_PROFIT_TON),
        SnipeSignal.net_profit_ton > 0,
        SnipeSignal.deal_price > 0,
        SnipeSignal.floor_price > 0,
        SnipeSignal.deal_price < SnipeSignal.floor_price,
    )
    needle = (q or "").strip()
    if needle:
        pattern = f"%{needle}%"
        stmt = stmt.where(
            or_(
                SnipeSignal.gift_name.ilike(pattern),
                SnipeSignal.model.ilike(pattern),
            )
        )
    if min_price is not None:
        stmt = stmt.where(SnipeSignal.deal_price >= float(min_price))
    if max_price is not None:
        stmt = stmt.where(SnipeSignal.deal_price <= float(max_price))
    stmt = (
        stmt.order_by(SnipeSignal.created_at.desc(), SnipeSignal.id.desc())
        .limit(fetch_limit)
    )

    async with db.session() as session:
        rows = list((await session.execute(stmt)).scalars())

    selected: list[SnipeSignal] = []
    per_gift: dict[str, int] = {}
    for sig in rows:
        used = per_gift.get(sig.gift_name, 0)
        if used >= cap:
            continue
        per_gift[sig.gift_name] = used + 1
        selected.append(sig)
        if len(selected) >= limit:
            break
    return selected


def _normalize_getgems_url(url: str) -> str:
    """Очищает Getgems-ссылку: убирает ?price= и переводит 0:hex в EQ..."""
    if not url:
        return ""
    if "getgems.io/nft/" in url:
        base = url.split("?")[0]
        prefix, _, addr = base.partition("getgems.io/nft/")
        if addr:
            return f"{prefix}getgems.io/nft/{friendly_address(addr)}"
    return url


def _normalize_portals_url(url: str) -> str:
    """Оживляет старые ссылки Portals — ведём на конкретный гифт.

    Актуальный формат — deep-link из share-генератора самого Portals:
    t.me/portals_market_bot/market?startapp=gift_<id>. В БД лежат два
    устаревших вида: t.me/portals/market?startapp=to_market#<id> (id
    спрятан в «фрагменте» — его вытаскиваем) и мёртвые
    portals-market.com/nft/<slug>_<num> (id там не TON-шный, восстанавливать
    нечего) — их хотя бы переводим на живой маркет вместо пустой страницы.
    """
    if not url:
        return url
    if "portals-market.com/" in url:
        return PORTALS_MARKET_URL
    if "t.me/portals/market" in url and "#" in url:
        item_id = url.rsplit("#", 1)[1].strip()
        if item_id:
            return PORTALS_ITEM_URL.format(id=item_id)
    return url


def _signal_to_dict(sig: SnipeSignal) -> dict[str, Any]:
    clean_addr = friendly_address(sig.item_address) if sig.item_address else None
    market_url = _normalize_portals_url(_normalize_getgems_url(sig.marketplace_url))
    img = sig.image_url
    if not img or "/collection/" in img:
        num_str = "".join(filter(str.isdigit, sig.lot_number or ""))
        if num_str:
            img = fragment_model_image(sig.gift_name, int(num_str))
        elif not img:
            preset = GIFT_PRESETS.get(sig.gift_name) or {}
            img = preset.get("image")
    return {
        "gift_name": sig.gift_name,
        "lot_number": sig.lot_number,
        "floor_price": sig.floor_price,
        "deal_price": sig.deal_price,
        "discount_pct": sig.discount_pct,
        "net_profit_ton": sig.net_profit_ton,
        "marketplace_url": market_url,
        "image_url": img,
        "item_address": clean_addr,
        "source": sig.source,
        # Атрибуты конкретного NFT: чем лоты отличаются друг от друга.
        "model": sig.model,
        "backdrop": sig.backdrop,
        "symbol": sig.symbol,
        "floor_basis": sig.floor_basis,
        "tonkeeper_url": (
            f"https://app.tonkeeper.com/nft/{clean_addr}" if clean_addr else None
        ),
        "is_hot": sig.discount_pct >= get_settings().HOT_THRESHOLD_PCT,
        "created_at": sig.created_at.isoformat() + "Z",
    }


async def floors_payload(
    market: Any, settings: Any, db: Any | None = None
) -> list[dict[str, Any]]:
    """Флоры коллекций с реальными именами ассетов и ссылками Getgems.

    Общий билдер для авторизованного ``/api/gifts/floors`` и публичного
    ``/api/demo/floors``: рыночные данные не персональные. Если передана
    ``db``, попутно копится история флоров — из неё считается настоящее
    изменение за 24ч (``None``, пока истории меньше суток).
    """
    floors = await market.get_floors()
    catalog = market.get_catalog() if hasattr(market, "get_catalog") else {}
    rate = (
        await market.fetch_ton_usd_rate()
        if hasattr(market, "fetch_ton_usd_rate")
        else settings.TON_USD_RATE
    )

    changes: dict[str, float | None] = {}
    source = (settings.MARKET_SOURCE or "").strip().lower()
    if db is not None and floors and source in {"giftdataset", "real", "getgems"}:
        current = {
            name: float(info["floor"])
            for name, info in floors.items()
            if info.get("floor")
        }
        try:
            await db.record_floor_snapshot(current, settings.FLOOR_SNAPSHOT_INTERVAL_SEC)
            changes = await db.floor_changes(
                current, min_age_hours=settings.FLOOR_CHANGE_MIN_AGE_HOURS
            )
        except Exception:  # noqa: BLE001 — история не должна ломать флоры
            logger.exception("Floor history update failed")

    # Топ-бида автобая MRKT (из data/mrkt_bids.json, обновляет хук).
    # Пусто, если MRKT-цепочка не работает — клиент просто скрывает фильтр.
    mrkt_bids: dict[str, dict[str, float]] = {}
    try:
        mrkt_bids = await market._mrkt_top_bids()
    except Exception:  # noqa: BLE001 — бида не должны ломать флоры
        mrkt_bids = {}

    result: list[dict[str, Any]] = []
    for name, info in floors.items():
        entry = catalog.get(name) or {}
        preset = GIFT_PRESETS.get(name) or {}
        address = entry.get("address", preset.get("address"))
        verified = entry.get("address_verified", bool(address))
        if address and verified:
            getgems_url = f"https://getgems.io/collection/{friendly_address(address)}"
        else:
            getgems_url = "https://getgems.io/search?text=" + quote(name)
        change = changes.get(name, info.get("change_24h")) if changes else info.get("change_24h")
        # Ссылки на офферы ключевых платформ (Getgems/Portals/MRKT):
        # у Getgems — страница коллекции, у Portals и MRKT — их официальный
        # Mini App (Tonnel убран: их API закрыт Cloudflare, лотов нет).
        offer_urls = {
            "getgems": getgems_url,
            "portals": PORTALS_MARKET_URL,
            "mrkt": MRKT_MINIAPP_URL,
        }
        bid_info = mrkt_bids.get(name)
        result.append(
            {
                "name": name,
                # Реальное имя NFT-коллекции (Plush Pepes, Ion Gems), без
                # выдуманных «Rare Meme»-группировок.
                "asset": entry.get("asset") or preset.get("asset") or name,
                "floor": info["floor"],
                "floor_usd": round(info["floor"] * rate, 2),
                "change_24h": change,
                "liquidity": info.get("liquidity") or {"label": "MED", "value": "med"},
                "image": entry.get("image") or preset.get("image"),
                "short_name": entry.get("short_name"),
                "dataset_name": entry.get("dataset_name") or name,
                "collection_address": address,
                "getgems_url": getgems_url,
                # Флоры конкретных маркетов и ссылки на их офферы.
                "market_floors": info.get("market_floors") or {},
                # Живой топ-бид автобая MRKT (для фильтра связок).
                "mrkt_bid": bid_info["price"] if bid_info else None,
                "offer_urls": offer_urls,
            }
        )
    return result


async def models_payload(market: Any, name: str) -> dict[str, Any]:
    """Модели коллекции и флоры по ним — данные для калькулятора.

    Калькулятор считает не от общего флора коллекции (он равен самому
    дешёвому лоту среди всех моделей), а от флора выбранной модели.
    """
    floors = await market.get_floors()
    info = floors.get(name) or {}
    prices = await market.get_trait_prices(name)
    models: list[dict[str, Any]] = []
    if prices is not None and prices.models:
        get_image = getattr(market, "portal_model_image", None)
        # Рендеры моделей из датасета (официальные webp-ассеты Telegram) —
        # фолбэк, когда Portals еще не встречал такую модель в листингах.
        dataset_images: dict[str, str] = {}
        dataset_loader = getattr(market, "dataset_model_images", None)
        if dataset_loader is not None:
            try:
                dataset_images = await dataset_loader(name)
            except Exception:  # noqa: BLE001 — картинки не должны ломать модели
                dataset_images = {}
        models = [
            {
                "name": model,
                "floor": floor,
                # Точный рендер из Portals, иначе официальный webp модели.
                "image": (
                    get_image(name, model)
                    if get_image
                    else None
                )
                or dataset_images.get(model.strip().replace("_", " ").casefold()),
            }
            for model, floor in sorted(prices.models.items(), key=lambda item: item[1])
        ]
    return {
        "name": name,
        "floor": info.get("floor"),
        "models": models,
        "source": "dataset" if models else "none",
    }


@router.get("/rates")
async def get_market_rates(request: Request) -> dict[str, Any]:
    """Живой курс TON/USD."""
    market = request.app.state.market
    rate = (
        await market.fetch_ton_usd_rate()
        if hasattr(market, "fetch_ton_usd_rate")
        else request.app.state.settings.TON_USD_RATE
    )
    return {"ton_usd": rate}


@router.get("/models")
async def get_models(
    request: Request,
    name: str = Query(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Модели подарка и их флоры (авторизованный режим Mini App)."""
    return await models_payload(request.app.state.market, name)


@router.get("/floors")
async def get_floors(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Текущие флоры коллекций: цена TON, USD, изменение 24ч, ликвидность."""
    return await floors_payload(
        request.app.state.market, request.app.state.settings, request.app.state.db
    )


@router.get("/feed")
async def get_feed(
    request: Request,
    user: User = Depends(get_current_user),
    q: str | None = Query(default=None, max_length=64, description="Коллекция или модель"),
    min_price: float | None = Query(default=None, ge=0, description="Цена лота от, TON"),
    max_price: float | None = Query(default=None, ge=0, description="Цена лота до, TON"),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Лента выгодных лотов (новее — выше) с фильтрами по коллекции и цене.

    Только сделки: цена ниже эталона и положительный профит после комиссий.
    Наценки над флором не отдаются, даже если они остались в базе.
    """
    signals = await query_feed(
        request.app.state.db,
        request.app.state.settings,
        q=q,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
    )
    return [_signal_to_dict(sig) for sig in signals]


__all__ = ["router", "floors_payload", "models_payload", "query_feed"]
