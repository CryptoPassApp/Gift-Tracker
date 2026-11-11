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
import re
import time
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
    is_auction: bool = False     # лот-аукцион: deal_price = текущая ставка
    auction_end_ts: float | None = None  # unix ts конца (Fragment; Getgems не отдает)

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
            "is_auction": self.is_auction,
            "auction_end_ts": self.auction_end_ts,
            "created_at": self.created_at.isoformat() + "Z",
        }


def reprice_pricing(
    floor_price: float,
    deal_price: float,
    *,
    source: str | None,
    floor_basis: str | None,
    settings: Settings,
) -> tuple[float, float]:
    """Дисконт (%) и чистый профит (TON) для пары цен — формула build_signal.

    Отдельная функция, чтобы перепроверка ленты считала обновлённую
    карточку ровно той же математикой, что и первичный сигнал: иначе
    карточка после живого апдейта цены начала бы врать относительно новых.
    """
    if floor_price <= 0 or deal_price <= 0:
        return 0.0, 0.0
    # Дисконт может быть отрицательным (лот дороже эталона) — такие лоты
    # в ленту не попадают, их отсекает scan_once до сохранения.
    discount = round(((floor_price - deal_price) / floor_price) * 100, 1)
    # Эталон "mrkt_bid" — выход через ордер-автобай MRKT: комиссия их маркета.
    if (floor_basis or "") == "mrkt_bid":
        fee = getattr(settings, "MRKT_FEE", 0.05)
    elif hasattr(settings, "fee_for_source"):
        fee = settings.fee_for_source(source)
    else:
        fee = getattr(settings, "GETGEMS_FEE", 0.05)
    net_profit = round((floor_price * (1 - fee)) - deal_price - settings.TON_GAS, 2)
    return discount, net_profit


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

    discount, net_profit = reprice_pricing(
        floor_price, lot.price, source=lot.source, floor_basis=floor_basis, settings=settings
    )
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
        is_auction=lot.is_auction,
        auction_end_ts=lot.auction_end_ts,
    )


def is_feed_worthy(signal: Signal, min_profit_ton: float = 0.0) -> bool:
    """Лот попадает в ленту только если он реально выгоден.

    Лента снайпера — не список всех листингов маркета: в ней только лоты,
    которые дешевле эталона и дают чистый профит выше порога. Порог нужен,
    потому что лоты с +0.03 TON после комиссии маркетплейса — это ноль,
    а не сделка: деньги съест комиссия и газ. Понижать порог до «любого
    плюса» нельзя — лента снова забивается пылью (+0.02..0.15 TON).
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
        # Периодическая перепроверка ленты (мёртвые сделки удаляются сами).
        self._last_feed_verify: float = 0.0

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
        # Периодическая перепроверка ленты: мёртвые сделки (лот ушёл в
        # аукцион, подорожал, продан) вычищаются сами — иначе карточка
        # висит днями с ценой, которой больше нет.
        now_mono = time.monotonic()
        if now_mono - self._last_feed_verify >= self.settings.FEED_VERIFY_INTERVAL_SEC:
            self._last_feed_verify = now_mono
            try:
                await self.verify_feed()
            except Exception:  # noqa: BLE001 — проверка не должна ломать скан
                logger.exception("Feed verification failed")

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
    async def verify_feed(self) -> int:
        """Перепроверка недавних Portals-сделок против живых лотов.

        Portals не отличает аукцион от продажи в /nfts/search (price там и
        для аукциона = текущая ставка), поэтому свежие записи ленты
        переспрашиваются напрямую: если лот больше не ``listed`` — запись
        удаляется; если цена уехала — карточка ПЕРЕСЧИТЫВАЕТСЯ под живую
        цену (вверх за допуск — удаляется: это уже не та сделка). Сетевые
        сбои ничего не меняют — только подтверждённые изменения.
        Вторая проверка — сверка с текущими флорами обеих площадок: эталон
        на карточке обновляется вслед за рынком, а сделка, оказавшаяся
        выше нового эталона, удаляется.
        """
        signals = await self.db.signals_for_verification("portals", hours=48)
        removed = 0
        updated = 0
        for signal in signals:
            match = re.search(r"startapp=gift_([0-9a-fA-F-]{36})", signal.marketplace_url or "")
            if not match:
                continue
            item = await self.market.fetch_portals_item(match.group(1))
            if item is None:
                continue
            if str(item.get("status") or "") != "listed":
                await self.db.delete_snipe_signal(signal.id)
                removed += 1
                continue
            try:
                live_price = float(item.get("price"))
            except (TypeError, ValueError):
                continue
            result = await self._apply_live_price(signal, live_price)
            if result == "removed":
                removed += 1
            elif result == "updated":
                updated += 1
        # Истёкшие таймеры аукционов: Fragment отдаёт время конца прямо в
        # сетке, поэтому истёкший аукцион (выигран или закрыт без ставок)
        # чистится БЕЗ сети — по одному полю. Getgems-аукционы живут до
        # обычной сверки: TonAPI ответит «sale пропал».
        for source in ("getgems", "fragment"):
            for signal in await self.db.signals_for_verification(source, hours=48):
                if not signal.is_auction or not signal.auction_end_ts:
                    continue
                try:
                    expired = float(signal.auction_end_ts) <= time.time()
                except (TypeError, ValueError):
                    continue
                if expired:
                    await self.db.delete_snipe_signal(signal.id)
                    removed += 1
        # То же для getgems-лотов, но через TonAPI: у Portals был свой
        # факт-чекер, а getgems-карточки жили в ленте даже когда лот уже
        # куплен, ушёл в аукцион или продавец поднял цену. TonAPI отвечает
        # sale-объектом только у лота, который реально стоит на продаже.
        for signal in await self.db.signals_for_verification("getgems", hours=48):
            if not signal.item_address:
                continue
            item = await self.market.fetch_tonapi_item(signal.item_address)
            if item is None:
                continue  # сеть/лимиты — наугад ничего не удаляем
            sale = item.get("sale") or {}
            if not sale:
                # продан/снят с продажи (аукцион закрылся без ставок — тоже сюда).
                await self.db.delete_snipe_signal(signal.id)
                removed += 1
                continue
            if not signal.is_auction and str(sale.get("type") or "") == "AuctionSale":
                # фикс-цена сконвертирована в аукцион: сделка умерла.
                await self.db.delete_snipe_signal(signal.id)
                removed += 1
                continue
            if not signal.is_auction:
                # Аукцион под видом фикса: TonAPI больше не отдает sale.type,
                # аукционный контракт узнаем по интерфейсу (nft_auction_v1) —
                # «цена» в sale это текущая ставка, покупкой она не является.
                # Аукционные карточки (is_auction) это РАБОЧИЙ формат: их
                # перепроверяет сверка эталона ниже (ставка перебита > эталона
                # → удаление), здесь их не трогаем.
                sale_addr = str(sale.get("address") or "")
                if sale_addr and await self.market.is_auction_sale(sale_addr) is True:
                    await self.db.delete_snipe_signal(signal.id)
                    removed += 1
                    continue
            try:
                live_price = int(sale["price"]["value"]) / 1e9
            except (KeyError, TypeError, ValueError):
                continue
            result = await self._apply_live_price(
                signal, live_price, allow_rise_removal=not signal.is_auction
            )
            if result == "removed":
                removed += 1
            elif result == "updated":
                updated += 1
        # Сверка с ТЕКУЩИМИ флорами: рынок едет — сделка, пойманная ниже флора,
        # после общего пересчёта рынка оказывается выше нового флора. Такая строка
        # уже не сделка и только вводит в заблуждение (карточка показывает скидку
        # от старого флора). Флоры берём из общего кэша — те же, что ест скан.
        # Model-базис перепроверяем тем же reference_price: его эталон может
        # упасть, когда краулер увидел реальные аски модели (датасет врёт).
        # Сделка ниже нового эталона остаётся, но зачёркнутый флор и дисконт
        # на карточке обновляются: «46 TON» при живом флоре 45 — то же враньё,
        # что и устаревшая цена лота.
        recent: list[Any] = []
        seen_ids: set[int] = set()
        for source in ("getgems", "portals", "fragment"):
            for sig in await self.db.signals_for_verification(source, hours=48):
                if sig.id not in seen_ids:
                    seen_ids.add(sig.id)
                    recent.append(sig)
        if recent:
            try:
                floors = await self.market.get_floors()
            except Exception:  # noqa: BLE001 — нет флоров, нет сверки, вслепую не удаляем
                floors = {}
            for signal in recent:
                ref = 0.0
                ref_basis: str | None = None
                if signal.floor_basis == "collection":
                    info = floors.get(signal.gift_name) or {}
                    ref = (info.get("market_floors") or {}).get(signal.source)
                elif (signal.floor_basis or "").startswith("model:"):
                    stub = Lot(
                        gift_name=signal.gift_name,
                        lot_number=signal.lot_number,
                        price=signal.deal_price,
                        marketplace_url=signal.marketplace_url or "",
                        item_address=signal.item_address,
                        image_url=signal.image_url,
                        source=signal.source,
                        model=signal.model,
                        backdrop=signal.backdrop,
                        symbol=signal.symbol,
                    )
                    try:
                        ref, ref_basis = await self.market.reference_price(stub, floors)
                    except Exception:  # noqa: BLE001 — сверка не должна ронять скан
                        continue
                try:
                    ref = float(ref or 0.0)
                except (TypeError, ValueError):
                    continue
                if ref > 0 and signal.deal_price > 0 and signal.deal_price >= ref:
                    await self.db.delete_snipe_signal(signal.id)
                    removed += 1
                    continue
                # mrkt_bid-эталон — это чужая ставка покупателя, а не флор
                # карточки: её не показываем и в зачёркнутый флор не пишем.
                if ref_basis == "mrkt_bid":
                    continue
                try:
                    stored_floor = round(float(signal.floor_price or 0.0), 2)
                except (TypeError, ValueError):
                    continue
                if ref > 0 and abs(round(ref, 2) - stored_floor) >= 0.005:
                    discount, profit = reprice_pricing(
                        ref,
                        float(signal.deal_price or 0.0),
                        source=signal.source,
                        floor_basis=signal.floor_basis,
                        settings=self.settings,
                    )
                    await self.db.update_snipe_signal_pricing(
                        signal.id,
                        deal_price=float(signal.deal_price or 0.0),
                        discount_pct=discount,
                        net_profit_ton=profit,
                        floor_price=round(ref, 2),
                    )
                    updated += 1

        if removed or updated:
            logger.info(
                "Feed verification: %d stale deal(s) removed, %d card(s) repriced to live market",
                removed,
                updated,
            )
        return removed

    async def _apply_live_price(
        self, signal: Any, live_price: float, *, allow_rise_removal: bool = True
    ) -> str | None:
        """Сверяет карточку с живой ценой лота: "removed" | "updated" | None.

        Лот жив, но цена на маркете уехала. Вверх за допуск — продавец
        передумал, это уже не та сделка: карточка удаляется. Вниз (любой
        размер) или вверх в пределах допуска — карточка ПЕРЕСЧИТЫВАЕТСЯ
        под живую цену: показывать «46 TON», когда на маркете уже 45 —
        ровно то, за что ленту ругают; а сделку, подешевевшую на 100 TON,
        удалять вместо обновления — терять лучшую цену входа.
        Для аукционов (allow_rise_removal=False) рост ставки — норма:
        карточку обновляем всегда, а удаляет её сверка эталона, когда
        ставка перебила флор.
        """
        try:
            stored = float(signal.deal_price)
        except (TypeError, ValueError):
            return None
        if stored <= 0 or live_price <= 0:
            return None
        if live_price > stored and allow_rise_removal:
            deviation = (live_price - stored) / stored
            if deviation > self.settings.FEED_VERIFY_PRICE_TOLERANCE:
                # продавец поднял цену за допуск: это уже не та сделка.
                await self.db.delete_snipe_signal(signal.id)
                return "removed"
        if abs(round(live_price, 2) - round(stored, 2)) < 0.005:
            return None  # цена не изменилась
        discount, profit = reprice_pricing(
            float(signal.floor_price or 0.0),
            live_price,
            source=signal.source,
            floor_basis=signal.floor_basis,
            settings=self.settings,
        )
        await self.db.update_snipe_signal_pricing(
            signal.id,
            deal_price=live_price,
            discount_pct=discount,
            net_profit_ton=profit,
        )
        return "updated"

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
                    is_auction=signal.is_auction,
                    auction_end_ts=signal.auction_end_ts,
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


__all__ = ["Signal", "SniperEngine", "build_signal", "evaluate_lot", "is_feed_worthy", "reprice_pricing"]
