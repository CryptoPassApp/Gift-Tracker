"""Публичный демо-эндпоинт: /api/demo/feed — данные без авторизации.

Используется для быстрой проверки развертывания (curl/healthcheck)
и как источник данных, если Mini App открыт в Telegram, но авторизация
initData не обязательна. Возвращает свежесгенерированные mock-сигналы.
"""
from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, Request

from bot.services.market_api import (
    GIFT_NAMES,
    GIFT_PRESETS,
    Lot,
    fragment_model_image,
    getgems_collection_url,
)
from bot.services.sniper_engine import evaluate_lot

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/feed")
async def demo_feed(request: Request, count: int = 8) -> list[dict[str, Any]]:
    """Генерирует ``count`` случайных снайперских сигналов (без БД и авторизации)."""
    settings = request.app.state.settings
    market = request.app.state.market

    floors = await market.get_floors()
    signals: list[dict[str, Any]] = []

    for _ in range(max(1, min(count, 20))):
        gift = random.choice(GIFT_NAMES)
        floor_info = floors.get(gift)
        if floor_info is None:
            continue
        floor = float(floor_info["floor"])

        discount = random.uniform(0.16, 0.43)
        model_number = random.randint(1, GIFT_PRESETS[gift]["supply"])
        lot = Lot(
            gift_name=gift,
            lot_number=f"#{model_number}",
            price=round(floor * (1 - discount), 2),
            marketplace_url=getgems_collection_url(gift),
            image_url=fragment_model_image(gift, model_number),
            model_number=model_number,
            source="getgems",
        )
        signal = evaluate_lot(lot, floor, settings)
        if signal is not None:
            signals.append(signal.as_dict())

    signals.sort(key=lambda s: s["discount_pct"], reverse=True)
    return signals


__all__ = ["router"]
