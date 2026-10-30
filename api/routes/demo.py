"""Демо-эндпоинты: /api/demo/feed и публичные флоры без авторизации.

Для Mini App, когда initData пуста (открытие вне Telegram),
фронтенд ходит сюда. Отдаёт точно такие же структуры
данных: те же сигналы снайпера из базы и те же
флоры-коллекций с подсчётом дельты.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from api.rate_limiter import InMemoryRateLimiter
from api.routes.gifts import _signal_to_dict, floors_payload, models_payload, query_feed

router = APIRouter(prefix="/demo", tags=["demo"])

# Защита от бесплатного DoS: ограничение запросов к публичным эндпоинтам на IP
demo_limiter = InMemoryRateLimiter(max_requests=60, window_sec=60)


def _demo_signal_to_dict(sig: Any) -> dict[str, Any]:
    """Сериализация для публичного демо: скрывает прямые диплинки на кошелек для защиты платной модели."""
    d = _signal_to_dict(sig)
    d["tonkeeper_url"] = None
    d["item_address"] = None
    return d


@router.get("/floors")
async def demo_floors(request: Request) -> list[dict[str, Any]]:
    """Публичные флоры коллекций без авторизации, аналогично /api/gifts/floors."""
    demo_limiter.check(request)
    return await floors_payload(
        request.app.state.market, request.app.state.settings, request.app.state.db
    )


@router.get("/models")
async def demo_models(
    request: Request,
    name: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    """Публичные цены моделей подарка (для превью вне Telegram)."""
    demo_limiter.check(request)
    return await models_payload(request.app.state.market, name)


@router.get("/feed")
async def demo_feed(
    request: Request,
    count: int = Query(default=8, ge=1, le=50),
    q: str | None = Query(default=None, max_length=64, description="Поиск по имени"),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """Публичная демо-лента горячих предложений с защитой платной модели и лимитом запросов."""
    demo_limiter.check(request)
    signals = await query_feed(
        request.app.state.db,
        request.app.state.settings,
        q=q,
        min_price=min_price,
        max_price=max_price,
        limit=count,
    )
    return [_demo_signal_to_dict(sig) for sig in signals]


@router.get("/rates")
async def demo_rates(request: Request) -> dict[str, float]:
    """Динамический курс TON/USD (из TonAPI или кэша)."""
    demo_limiter.check(request)
    market = getattr(request.app.state, "market", None)
    if market is not None:
        rate = await market.fetch_ton_usd_rate()
    else:
        rate = 1.24
    return {"ton_usd": rate}


__all__ = ["router"]
