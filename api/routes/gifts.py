"""Эндпоинты рыночных данных: /api/gifts/floors и /api/gifts/feed."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from api.dependencies import get_current_user
from bot.config import get_settings
from bot.models import SnipeSignal, User
from bot.services.market_api import GIFT_PRESETS, getgems_collection_url

router = APIRouter(prefix="/gifts", tags=["gifts"])


def _signal_to_dict(sig: SnipeSignal) -> dict[str, Any]:
    return {
        "gift_name": sig.gift_name,
        "lot_number": sig.lot_number,
        "floor_price": sig.floor_price,
        "deal_price": sig.deal_price,
        "discount_pct": sig.discount_pct,
        "net_profit_ton": sig.net_profit_ton,
        "marketplace_url": sig.marketplace_url,
        "image_url": sig.image_url,
        "item_address": sig.item_address,
        "source": sig.source,
        "tonkeeper_url": (
            f"https://app.tonkeeper.com/nft/{sig.item_address}" if sig.item_address else None
        ),
        "is_hot": sig.discount_pct >= get_settings().HOT_THRESHOLD_PCT,
        "created_at": sig.created_at.isoformat() + "Z",
    }


@router.get("/floors")
async def get_floors(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Текущие флоры 8 коллекций: цена TON, USD, изменение 24ч, ликвидность."""
    market = request.app.state.market
    settings = request.app.state.settings
    floors = await market.get_floors()

    result: list[dict[str, Any]] = []
    for name, preset in GIFT_PRESETS.items():
        info = floors.get(name)
        if info is None:
            continue
        result.append(
            {
                "name": name,
                "asset": preset.get("asset"),
                "collection": preset["collection"],
                "floor": info["floor"],
                "floor_usd": round(info["floor"] * settings.TON_USD_RATE, 2),
                "change_24h": info["change_24h"],
                "liquidity": info["liquidity"],
                "image": preset.get("image"),
                "collection_address": preset.get("address"),
                "getgems_url": getgems_collection_url(name),
            }
        )
    return result


@router.get("/feed")
async def get_feed(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Лента последних сигналов снайпера (новее — выше)."""
    settings = request.app.state.settings
    db = request.app.state.db

    async with db.session() as session:
        stmt = (
            select(SnipeSignal)
            .order_by(SnipeSignal.created_at.desc(), SnipeSignal.id.desc())
            .limit(settings.FEED_HISTORY_LIMIT)
        )
        signals = list((await session.execute(stmt)).scalars())

    return [_signal_to_dict(sig) for sig in signals]


__all__ = ["router"]
