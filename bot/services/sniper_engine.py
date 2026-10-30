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
from typing import Any, Callable

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
    model: str | None = None     # атрибут Model конкретного NFT (Barcelona, ...)
    backdrop: str | None = None  # атрибут Backdrop (Lemongrass, ...)
    symbol: str | None = None    # атрибут Symbol (Cap, ...)
    floor_basis: str | None = None  # "collection" | "model:<имя>"

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
            "model": self.model,
            "backdrop": self.backdrop,
            "symbol": self.symbol,
            "floor_basis": self.floor_basis,
            "created_at": self.created_at.isoformat() + "Z",
        }


def build_signal(
    lot: Lot, floor_price: float, settings: Settings, *, floor_basis: str | None = None
) -> Signal | None:
    """Строит сигнал из лота по эталонной цене ``floor_price``.

    Эталон приходит из ``MarketAPI.reference_price``: флор модели лота, а
    если он неизвестен — флор коллекции. Сигнал строится без порога, чтобы
    вызывающий код сам решил, что делать (лента / пуш).
    """
    if floor_price <= 0 or lot.price <= 0:
        return None

    # Дисконт может быть отрицательным (лот дороже эталона) — такие лоты
    # в ленту не попадают, их отсекает scan_once до сохранения.
    discount = round(((floor_price - lot.price) / floor_price) * 100, 1)
    # Эталон "mrkt_bid" — выход через ордер-автобай MRKT: комиссия их маркета.
    if (floor_basis or "") == "mrkt_bid":
        fee = getattr(settings, "MRKT_FEE", 0.05)
    elif hasattr(settings, "fee_for_source"):
        fee = settings.fee_for_source(lot.source)
    else:
        fee = getattr(settings, "GETGEMS_FEE", 0.05)
    net_profit = round((floor_price * (1 - fee)) - lot.price - settings.TON_GAS, 2)
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
        model=lot.model,
        backdrop=lot.backdrop,
        symbol=lot.symbol,
        floor_basis=floor_basis,
    )


def is_feed_worthy(signal: Signal, min_profit_ton: float = 0.0) -> bool:
    """Лот попадает в ленту только если он реально выгоден.

    Лента снайпера — не список всех листингов маркета: в ней только лоты,
    которые дешевле эталона и дают чистый профит выше порога. Порог нужен,
    потому что лоты с +0.03 TON после комиссии маркетплейса — это ноль,
    а не сделка: деньги съест комиссия и газ.
    """
    return (
        signal.discount_pct > 0
        and signal.net_profit_ton > 0
        and signal.net_profit_ton >= float(min_profit_ton)
    )


def evaluate_lot(lot: Lot, floor_price: float, settings: Settings) -> Signal | None:
    """Применяет формулу арбитража к лоту.

    Возвращает ``Signal`` если дисконт >= SNIPER_THRESHOLD_PCT, иначе ``None``.
    """
    signal = build_signal(lot, floor_price, settings)
    if signal is None or signal.discount_pct < settings.SNIPER_THRESHOLD_PCT:
        return None
    return signal


class SniperEngine:
    """Периодический сканер рынка: листинги -> сигналы -> пуш-рассылка.

    Дедупликация двухуровневая: L1 — in-memory LRU (быстро, в пределах
    процесса), L2 — таблица ``seen_lots`` в SQLite (переживает рестарт).
    """

    def __init__(
        self,
        market: MarketAPI,
        db: Database,
        notifier: Notifier,
        settings: Settings,
        *,
        on_scan: Callable[[], None] | None = None,
    ) -> None:
        self.market = market
        self.db = db
        self.notifier = notifier
        self.settings = settings
        # Колбэк для heartbeat: вызывается после каждого успешного прохода.
        self.on_scan = on_scan
        # L1-кэш дедупликации: один и тот же лот не ходит в БД повторно.
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
        """Один проход: получить листинги, сохранить ВСЕ в ленту, разослать пуши."""
        floors = await self.market.get_floors()
        lots = await self.market.get_listings()

        signals: list[Signal] = []
        persisted = 0
        skipped_above_floor = 0
        for lot in lots:
            floor_info = floors.get(lot.gift_name)
            if floor_info is None:
                continue

            # Эталон — флор ИМЕННО ЭТОЙ модели лота: у Plush Pepe общий флор
            # коллекции 6470 TON, а модель Leonardo стоит 25500 TON.
            reference, basis = await self._reference(lot, floors)
            if reference <= 0:
                continue

            signal = build_signal(lot, reference, self.settings, floor_basis=basis)
            if signal is None:
                continue

            # В ленту попадают только лоты ниже эталона с профитом не меньше
            # FEED_MIN_PROFIT_TON после комиссий. Наценка — не сделка.
            if not is_feed_worthy(signal, self.settings.FEED_MIN_PROFIT_TON):
                skipped_above_floor += 1
                continue

            # Дедупликация — после оценки: лот, который сейчас дороже флора,
            # позже может стать выгодным, и его нельзя «сжигать» отметкой.
            if not await self._claim_lot(lot.marketplace_url):
                continue

            await self._persist_and_dispatch(signal)
            persisted += 1
            if signal.discount_pct >= self.settings.SNIPER_THRESHOLD_PCT:
                signals.append(signal)

        if persisted or skipped_above_floor:
            logger.info(
                "Scan: %d deal(s) persisted, %d listing(s) skipped above floor, "
                "%d above %.0f%% push threshold: %s",
                persisted,
                skipped_above_floor,
                len(signals),
                self.settings.SNIPER_THRESHOLD_PCT,
                ", ".join(f"{s.gift_name}/{s.model or '?'} -{s.discount_pct}%" for s in signals[:5]),
            )
        if self.on_scan is not None:
            try:
                self.on_scan()
            except Exception:  # noqa: BLE001 — телеметрия не должна ломать скан
                logger.exception("scan callback failed")
        return signals

    # ------------------------------------------------------------------
    # Внутренние шаги
    # ------------------------------------------------------------------
    async def _reference(self, lot: Lot, floors: dict[str, dict[str, Any]]) -> tuple[float, str]:
        """Эталонная цена лота: флор его модели, иначе флор коллекции."""
        getter = getattr(self.market, "reference_price", None)
        if getter is not None:
            reference, basis = await getter(lot, floors)
            if reference > 0:
                return reference, basis
        info = floors.get(lot.gift_name) or {}
        return float(info.get("floor") or 0.0), "collection"

    async def _claim_lot(self, url: str) -> bool:
        """Проверяет дедупликацию в L1 и атомарно фиксирует лот в БД."""
        if url in self._seen:
            return False

        claimed = await self.db.claim_seen_lot(url)
        # Помещаем URL в L1 в любом случае: повторные сканы не будут
        # дергать БД, даже если лот был отмечен еще до рестарта.
        self._remember_lot(url)
        return claimed
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
                    model=signal.model,
                    backdrop=signal.backdrop,
                    symbol=signal.symbol,
                    floor_basis=signal.floor_basis,
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


__all__ = ["Signal", "SniperEngine", "build_signal", "evaluate_lot", "is_feed_worthy"]
