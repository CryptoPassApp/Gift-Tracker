"""SniperEngine — расчет выгоды лотов, дедупликация, матчинг алертов.

Математика арбитража (ЧАСТЬ 2 ТЗ):
    Net Profit = (Floor * 0.95) - Listing - 0.08
    Discount(%) = round(((Floor - Listing) / Floor) * 100, 1)
    discount >= 15  -> Snipe Signal
    discount >= 30  -> Hot Liquidation (пушится всем, PRO — мгновенно)
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import Settings
from bot.database import Database
from bot.models import Alert, SnipeSignal, User
from bot.services.market_api import Lot, MarketAPI
from bot.services.notifier import Notifier

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """Оцененный лот, прошедший порог снайпера."""

    gift_name: str
    lot_number: str
    floor_price: float
    deal_price: float
    discount_pct: float
    net_profit_ton: float
    marketplace_url: str
    image_url: str | None        # рендер конкретной модели (CDN Fragment)
    item_address: str | None     # адрес NFT в TON — для диплинков Tonkeeper
    source: str                  # маркетплейс-источник оффера: getgems|tonnel|mrkt
    is_hot: bool
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "gift_name": self.gift_name,
            "lot_number": self.lot_number,
            "floor_price": self.floor_price,
            "deal_price": self.deal_price,
            "discount_pct": self.discount_pct,
            "net_profit_ton": self.net_profit_ton,
            "marketplace_url": self.marketplace_url,
            "image_url": self.image_url,
            "item_address": self.item_address,
            "source": self.source,
            "is_hot": self.is_hot,
            "created_at": self.created_at.isoformat() + "Z",
        }


def evaluate_lot(lot: Lot, floor_price: float, settings: Settings) -> Signal | None:
    """Применяет формулу арбитража к лоту.

    Возвращает ``Signal`` если дисконт >= SNIPER_THRESHOLD_PCT, иначе ``None``.
    """
    if floor_price <= 0 or lot.price <= 0:
        return None

    discount = round(((floor_price - lot.price) / floor_price) * 100, 1)
    if discount < settings.SNIPER_THRESHOLD_PCT:
        return None

    net_profit = round((floor_price * (1 - settings.GETGEMS_FEE)) - lot.price - settings.TON_GAS, 2)
    is_hot = discount >= settings.HOT_THRESHOLD_PCT

    return Signal(
        gift_name=lot.gift_name,
        lot_number=lot.lot_number,
        floor_price=round(floor_price, 2),
        deal_price=lot.price,
        discount_pct=discount,
        net_profit_ton=net_profit,
        marketplace_url=lot.marketplace_url,
        image_url=lot.image_url,
        item_address=lot.item_address,
        source=lot.source,
        is_hot=is_hot,
        created_at=datetime.utcnow(),
    )


class SniperEngine:
    """Периодический сканер рынка: листинги -> сигналы -> пуш-рассылка."""

    def __init__(self, market: MarketAPI, db: Database, notifier: Notifier, settings: Settings) -> None:
        self.market = market
        self.db = db
        self.notifier = notifier
        self.settings = settings
        # Кэш дедупликации: один и тот же лот не пушится дважды.
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Вечный цикл сканирования (запускается фоновым таском в боте)."""
        logger.info("SniperEngine started (interval=%.1fs)", self.settings.SCAN_INTERVAL_SEC)
        while True:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                logger.info("SniperEngine cancelled")
                raise
            except Exception:  # noqa: BLE001 — сканер не должен падать целиком
                logger.exception("SniperEngine scan failed")
            await asyncio.sleep(self.settings.SCAN_INTERVAL_SEC)

    async def scan_once(self) -> list[Signal]:
        """Один проход: получить листинги, оценить, сохранить, разослать."""
        floors = await self.market.get_floors()
        lots = await self.market.get_listings()

        signals: list[Signal] = []
        for lot in lots:
            if lot.marketplace_url in self._seen:
                continue
            self._remember_lot(lot.marketplace_url)

            floor_info = floors.get(lot.gift_name)
            if floor_info is None:
                continue

            signal = evaluate_lot(lot, float(floor_info["floor"]), self.settings)
            if signal is None:
                continue

            await self._persist_and_dispatch(signal)
            signals.append(signal)

        if signals:
            logger.info(
                "Scan produced %d signal(s): %s",
                len(signals),
                ", ".join(f"{s.gift_name} -{s.discount_pct}%" for s in signals),
            )
        return signals

    # ------------------------------------------------------------------
    # Внутренние шаги
    # ------------------------------------------------------------------
    def _remember_lot(self, url: str) -> None:
        self._seen[url] = None
        # Ограничиваем размер кэша дедупликации.
        while len(self._seen) > self.settings.SEEN_LOTS_CACHE_SIZE:
            self._seen.popitem(last=False)

    async def _persist_and_dispatch(self, signal: Signal) -> None:
        """Сохраняет сигнал в БД и рассылает уведомления."""
        async with self.db.session() as session:
            session.add(
                SnipeSignal(
                    gift_name=signal.gift_name,
                    lot_number=signal.lot_number,
                    floor_price=signal.floor_price,
                    deal_price=signal.deal_price,
                    discount_pct=signal.discount_pct,
                    net_profit_ton=signal.net_profit_ton,
                    marketplace_url=signal.marketplace_url,
                    image_url=signal.image_url,
                    item_address=signal.item_address,
                    source=signal.source,
                    created_at=signal.created_at,
                )
            )

            # HOT-лот уходит всем пользователям (PRO мгновенно, FREE +45с).
            if signal.is_hot:
                users = list((await session.execute(select(User))).scalars())
                await self.notifier.push_signal(signal, users)

            # Персональные алерты, подошедшие под триггеры.
            matches = await self._match_alerts(session, signal)
            if matches:
                await self.notifier.push_alert_matches(signal, matches)

    @staticmethod
    async def _match_alerts(session: Any, signal: Signal) -> list[Alert]:
        """Ищет активные алерты, под которые попадает сигнал.

        Условия: подарок совпадает, цена лота <= max_price,
        дисконт сигнала >= min_discount.
        """
        stmt = (
            select(Alert)
            .where(
                Alert.is_active.is_(True),
                Alert.gift_name == signal.gift_name,
                Alert.max_price >= signal.deal_price,
                Alert.min_discount <= signal.discount_pct,
            )
            .options(selectinload(Alert.user))
        )
        return list((await session.execute(stmt)).scalars())


__all__ = ["Signal", "SniperEngine", "evaluate_lot"]
