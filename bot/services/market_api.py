"""MarketAPI — источник рыночных данных для GiftTracker.

Две стратегии получения флоров и лотов:

* ``mock`` (по умолчанию) — автономный генератор со случайным блужданием
  флоров и потоком новых лотов. Позволяет запустить весь проект без
  внешних ключей и работает в Demo Mode Mini App.

* ``tonapi`` — попытка получить реальные флоры коллекций с tonapi.io
  (адреса коллекций задаются в ``TONAPI_COLLECTIONS``). При любой ошибке
  или отсутствии адреса — автоматический fallback на mock-генератор.

Результаты кэшируются в памяти на ``MARKET_CACHE_TTL_SEC`` секунд (5с),
чтобы десятки пользователей не создавали нагрузку на внешний API.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from bot.config import Settings

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Пресеты 8 подарков (ЧАСТЬ 2 ТЗ) на РЕАЛЬНЫХ ассетах подарков Telegram:
#   * image         — обложка коллекции (CDN Fragment, image/webp);
#   * address       — адрес коллекции в TON из метаданных TonAPI;
#   * fragment_slug — слаг для per-model рендеров
#                     https://nft.fragment.com/gift/{slug}-{номер}.webp —
#                     канонические картинки КАЖДОГО конкретного NFT с его
#                     узором/фоном (те же ассеты показывает Getgems);
#   * supply        — проверенный диапазон существующих номеров.
# Все URL проверены запросами (HTTP 200, image/webp).
# ----------------------------------------------------------------------
GIFT_PRESETS: dict[str, dict[str, Any]] = {
    "Plush Pepe": {
        "collection": "Rare Meme", "base_floor": 16.5,
        "asset": "Plush Pepes",
        "image": "https://nft.fragment.com/collection/plushpepe.webp",
        "address": "0:49381239d5509a69189b483a5d6876c1f184da49fb843a9fb784baa3f715c0b5",
        "fragment_slug": "plushpepe", "supply": 2024,
    },
    "Magic Hat": {
        "collection": "Cyber Magic", "base_floor": 24.0,
        "asset": "Genie Lamp",
        "image": "https://nft.fragment.com/gift/genielamp-1.webp",
        "address": None,
        "fragment_slug": "genielamp", "supply": 4999,
    },
    "Diamond Box": {
        "collection": "Royal Luxury", "base_floor": 45.0,
        "asset": "Ion Gems",
        "image": "https://nft.fragment.com/collection/iongem.webp",
        "address": "0:48ce225ca297b41f6e08c893f34586afafc03da36931940e3995968306c42514",
        "fragment_slug": "iongem", "supply": 3000,
    },
    "Heart Box": {
        "collection": "Romance", "base_floor": 8.2,
        "asset": "Trapped Heart",
        "image": "https://nft.fragment.com/collection/trappedheart.webp",
        "address": None,
        "fragment_slug": "trappedheart", "supply": 4999,
    },
    "Golden Star": {
        "collection": "Premium", "base_floor": 12.0,
        "asset": "Astral Shards",
        "image": "https://nft.fragment.com/collection/astralshard.webp",
        "address": "0:bd9ca89e71bfd232244a692f3caa7bbe73715a5c1adf16df17360844fe8f15e0",
        "fragment_slug": "astralshard", "supply": 4999,
    },
    "Spaceman": {
        "collection": "Cosmos", "base_floor": 38.5,
        "asset": "Heroic Helmets",
        "image": "https://nft.fragment.com/collection/heroichelmet.webp",
        "address": "0:b14482a1147ce6861d639a105581c3c6b86e80bdbbfd0118fec054cf4052b133",
        "fragment_slug": "heroichelmet", "supply": 3000,
    },
    "Birthday Cake": {
        "collection": "Celebration", "base_floor": 5.4,
        "asset": "Mousse Cakes",
        "image": "https://nft.fragment.com/collection/moussecake.webp",
        "address": "0:63cdd8b6ed923e45ef7738e2226b49bb626514f1df2e6c936c4ad65373ba8d26",
        "fragment_slug": "moussecake", "supply": 4999,
    },
    "Champagne": {
        "collection": "VIP Party", "base_floor": 19.8,
        "asset": "Perfume Bottles",
        "image": "https://nft.fragment.com/collection/perfumebottle.webp",
        "address": "0:13823580cafb1c06e1021b1352d87fd85411922381579774f0ec6c83563bd7b6",
        "fragment_slug": "perfumebottle", "supply": 3000,
    },
}

GIFT_NAMES: list[str] = list(GIFT_PRESETS.keys())


def fragment_model_image(gift_name: str, model_number: int) -> str:
    """Картинка конкретной модели подарка (узор/фон этого NFT).

    Fragment хостит канонические рендеры каждого номера:
    https://nft.fragment.com/gift/{slug}-{номер}.webp — именно их
    отображает Getgems на странице лота.
    """
    slug = GIFT_PRESETS[gift_name]["fragment_slug"]
    return f"https://nft.fragment.com/gift/{slug}-{model_number}.webp"


def getgems_collection_url(gift_name: str) -> str:
    """Живая ссылка на коллекцию подарка в Getgems (или поиск по имени)."""
    preset = GIFT_PRESETS.get(gift_name)
    address = preset.get("address") if preset else None
    if address:
        return f"https://getgems.io/collection/{address}"
    return "https://getgems.io/search?text=" + quote(gift_name)


def getgems_item_url(item_address: str) -> str:
    """Живая ссылка на конкретный NFT-лот в Getgems."""
    return f"https://getgems.io/nft/{item_address}"


@dataclass
class Lot:
    """Лот, появившийся на маркетплейсе."""

    gift_name: str
    lot_number: str
    price: float                 # цена продавца в TON
    marketplace_url: str
    listed_at: float = field(default_factory=time.time)  # unix timestamp
    item_address: str | None = None   # адрес NFT в TON (для диплинков Tonkeeper/Getgems)
    image_url: str | None = None      # рендер конкретной модели (CDN Fragment)
    model_number: int | None = None   # номер конкретного экземпляра (узор/фон)
    source: str = "getgems"           # маркетплейс-источник оффера: getgems|tonnel|mrkt


def _liquidity_for(gift_name: str) -> dict[str, str]:
    """Детерминированно назначает статус ликвидности (HIGH/MED/LOW)."""
    digest = int(hashlib.sha256(gift_name.encode("utf-8")).hexdigest(), 16)
    bucket = digest % 10
    if bucket >= 6:
        return {"label": "HIGH", "value": "high"}
    if bucket >= 3:
        return {"label": "MED", "value": "med"}
    return {"label": "LOW", "value": "low"}


class MarketAPI:
    """Асинхронный доступ к флорам и новым лотам с in-memory кэшем."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache_ttl = settings.MARKET_CACHE_TTL_SEC
        self._floors_cache: dict[str, dict[str, Any]] | None = None
        self._floors_cached_at: float = 0.0
        self._lock = asyncio.Lock()
        # Состояние mock random-walk: имя -> {floor, change24h}
        self._mock_state: dict[str, dict[str, float]] = {}
        self._init_mock_state()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------
    async def get_floors(self) -> dict[str, dict[str, Any]]:
        """Возвращает {имя подарка: {floor, change_24h, liquidity}} (кэш 5с)."""
        async with self._lock:
            now = time.monotonic()
            if self._floors_cache is not None and (now - self._floors_cached_at) < self._cache_ttl:
                return self._floors_cache

            floors: dict[str, dict[str, Any]] | None = None
            if self.settings.MARKET_SOURCE == "tonapi":
                floors = await self._fetch_tonapi_floors()

            if floors is None:
                floors = self._mock_floors()

            self._floors_cache = floors
            self._floors_cached_at = now
            return floors

    async def get_listings(self) -> list[Lot]:
        """Возвращает новые лоты с активных маркетплейсов.

        Активные источники задаются OFFER_SOURCES (getgems,tonnel,mrkt).
        Tonnel и MRKT делают реальные HTTP-запросы к своим API и требуют
        токен сессии (обе площадки за Cloudflare); без токена источник
        отключается с честным логом, поток продолжает идти с остальных.
        """
        lots: list[Lot] = []
        enabled = {s.strip().lower() for s in self.settings.OFFER_SOURCES.split(",") if s.strip()}

        if "tonnel" in enabled and self.settings.TONNEL_JWT:
            tonnel = await self._fetch_tonnel_listings()
            if tonnel is not None:
                lots.extend(tonnel)
        elif "tonnel" in enabled:
            logger.warning("OFFER_SOURCES includes tonnel, but TONNEL_JWT is empty — source disabled")

        if "mrkt" in enabled and self.settings.MRKT_AUTH_TOKEN:
            mrkt = await self._fetch_mrkt_listings()
            if mrkt is not None:
                lots.extend(mrkt)
        elif "mrkt" in enabled:
            logger.warning("OFFER_SOURCES includes mrkt, but MRKT_AUTH_TOKEN is empty — source disabled")

        # Getgems публичного REST-потока листингов не имеет (GraphQL закрыт
        # CloudFront для server-side клиентов) — поток через генератор.
        lots.extend(self._mock_listings(source="getgems"))
        return lots

    # ------------------------------------------------------------------
    # Mock-движок
    # ------------------------------------------------------------------
    def _init_mock_state(self) -> None:
        for name, preset in GIFT_PRESETS.items():
            drift = random.uniform(-0.12, 0.12)
            self._mock_state[name] = {
                "floor": round(preset["base_floor"] * (1 + drift), 2),
                "change24h": round(drift * 100, 1),
            }

    def _mock_floors(self) -> dict[str, dict[str, Any]]:
        """Случайное блуждание флоров в пределах ±25% от базового значения."""
        result: dict[str, dict[str, Any]] = {}
        for name, preset in GIFT_PRESETS.items():
            st = self._mock_state[name]
            move = random.uniform(-0.008, 0.008)
            floor = st["floor"] * (1 + move)
            floor = min(preset["base_floor"] * 1.25, max(preset["base_floor"] * 0.75, floor))
            st["floor"] = round(floor, 2)
            st["change24h"] = round(max(-35.0, min(35.0, st["change24h"] + move * 100)), 1)

            result[name] = {
                "floor": st["floor"],
                "change_24h": st["change24h"],
                "liquidity": _liquidity_for(name),
                "image": preset.get("image"),
                "address": preset.get("address"),
            }
        return result

    def _mock_listings(self, source: str = "getgems") -> list[Lot]:
        """0–2 новых лота за опрос: цены в диапазоне −10%…−45% от флора.

        Каждый лот — конкретная МОДЕЛЬ подарка: картинка берется per-item
        рендером Fragment (gift/{slug}-{номер}.webp) — тот же узор и фон,
        что у этого NFT на Getgems. Ссылка ведет на живую страницу
        коллекции в Getgems.
        """
        roll = random.random()
        count = 2 if roll > 0.85 else (1 if roll > 0.30 else 0)
        lots: list[Lot] = []
        for _ in range(count):
            name = random.choice(GIFT_NAMES)
            preset = GIFT_PRESETS[name]
            floor = self._mock_state[name]["floor"]
            discount = random.uniform(0.10, 0.45)
            price = round(floor * (1 - discount), 2)
            model_number = random.randint(1, preset["supply"])
            lots.append(
                Lot(
                    gift_name=name,
                    lot_number=f"#{model_number}",
                    price=price,
                    marketplace_url=getgems_collection_url(name),
                    image_url=fragment_model_image(name, model_number),
                    model_number=model_number,
                    source=source,
                )
            )
        return lots

    # ------------------------------------------------------------------
    # Адаптеры маркетплейсов (реальные эндпоинты; нужен токен сессии)
    # ------------------------------------------------------------------
    async def _fetch_tonnel_listings(self) -> list[Lot] | None:
        """Новые лоты Tonnel Network (api.tonnel.network, Bearer JWT).

        Токен получают из WebApp-сессии бота @tonnel_network_bot.
        Возвращает None при недоступности (Cloudflare/ошибка сети) —
        поток продолжается с остальных источников.
        """
        headers = {
            "Authorization": f"Bearer {self.settings.TONNEL_JWT}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                resp = await client.post(
                    "https://api.tonnel.network/v2/page/gifts",
                    json={"page": 1, "limit": 20, "sort": {"field": "price", "order": 1}},
                )
                resp.raise_for_status()
                items = resp.json().get("gifts") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Tonnel listings fetch failed (%s) — source skipped", exc)
            return None

        lots: list[Lot] = []
        for item in items:
            try:
                gift_name = str(item["gift_model"]["name"])
                if gift_name not in GIFT_PRESETS:
                    continue
                price = float(item["price"]) / 1e9  # nanoton -> TON
                if price <= 0:
                    continue
                model_number = int(item["gift_num"])
                lots.append(
                    Lot(
                        gift_name=gift_name,
                        lot_number=f"#{model_number}",
                        price=round(price, 2),
                        marketplace_url=f"https://t.me/tonnel_network_bot/gift?startapp={item.get('id', '')}",
                        item_address=item.get("gift_address") or None,
                        image_url=fragment_model_image(gift_name, model_number),
                        model_number=model_number,
                        source="tonnel",
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return lots

    async def _fetch_mrkt_listings(self) -> list[Lot] | None:
        """Новые лоты MRKT (API за Cloudflare; нужен токен сессии MRKT).

        Возвращает None при недоступности — поток продолжается с остальных
        источников. Структура ответа разбирается защитно: MRKT меняет
        поля без уведомлений, поэтому все поля проходят through try/except.
        """
        headers = {
            "Authorization": f"Bearer {self.settings.MRKT_AUTH_TOKEN}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                resp = await client.get(
                    "https://api.mrkt.io/api/gifts",
                    params={"limit": 20, "sort": "price_asc"},
                )
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("items") or payload.get("gifts") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("MRKT listings fetch failed (%s) — source skipped", exc)
            return None

        lots: list[Lot] = []
        for item in items:
            try:
                gift_name = str(item.get("collection") or item.get("name") or "")
                if gift_name not in GIFT_PRESETS:
                    continue
                price = float(item.get("price_ton") or item.get("price") or 0)
                if price <= 0:
                    continue
                model_number = int(item.get("number") or item.get("gift_num") or 0)
                lots.append(
                    Lot(
                        gift_name=gift_name,
                        lot_number=f"#{model_number}" if model_number else "#----",
                        price=round(price, 2),
                        marketplace_url=str(item.get("url") or "https://mrkt.io/gifts"),
                        item_address=item.get("address") or None,
                        image_url=fragment_model_image(gift_name, model_number) if model_number else GIFT_PRESETS[gift_name]["image"],
                        model_number=model_number or None,
                        source="mrkt",
                    )
                )
            except (TypeError, ValueError):
                continue
        return lots

    # ------------------------------------------------------------------
    # TonAPI (экспериментально, с fallback на mock)
    # ------------------------------------------------------------------
    async def _fetch_tonapi_floors(self) -> dict[str, dict[str, Any]] | None:
        """Пытается получить флоры коллекций с tonapi.io.

        Требует адреса коллекций в ``settings.TONAPI_COLLECTIONS``.
        Возвращает ``None`` при любой проблеме -> вызывающий код уйдет в mock.
        """
        collections: dict[str, str] = self.settings.TONAPI_COLLECTIONS
        if not collections:
            logger.debug("TONAPI_COLLECTIONS is empty — using mock floors")
            return None

        headers = {"Accept": "application/json"}
        if self.settings.TONAPI_KEY:
            headers["Authorization"] = f"Bearer {self.settings.TONAPI_KEY}"

        result: dict[str, dict[str, Any]] = {}
        try:
            async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
                for gift_name, address in collections.items():
                    if gift_name not in GIFT_PRESETS:
                        continue
                    resp = await client.get(
                        f"{self.settings.TONAPI_BASE_URL}/nfts/collections/{address}"
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    floor = self._extract_tonapi_floor(payload)
                    if floor is None or floor <= 0:
                        continue
                    result[gift_name] = {
                        "floor": round(floor, 2),
                        # TonAPI не отдает историю 24ч в этом эндпоинте — считаем от базы.
                        "change_24h": round(
                            (floor - GIFT_PRESETS[gift_name]["base_floor"])
                            / GIFT_PRESETS[gift_name]["base_floor"]
                            * 100,
                            1,
                        ),
                        "liquidity": _liquidity_for(gift_name),
                    }
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("TonAPI floors fetch failed (%s) — falling back to mock", exc)
            return None

        # Подарки без адреса в конфиге добираем mock-значениями.
        if len(result) < len(GIFT_PRESETS):
            for name, mock_floor in self._mock_floors().items():
                result.setdefault(name, mock_floor)
        return result

    @staticmethod
    def _extract_tonapi_floor(payload: dict[str, Any]) -> float | None:
        """Достает флор из разных возможных форматов ответа TonAPI."""
        if not isinstance(payload, dict):
            return None
        # Вариант 1: floor_prices: [{value: nanoton, ...}]
        floor_prices = payload.get("floor_prices")
        if isinstance(floor_prices, list) and floor_prices:
            value = floor_prices[0].get("value")
            if isinstance(value, (int, float)) and value > 0:
                return float(value) / 1e9
        # Вариант 2: floor_price в нанотонах или TON
        raw = payload.get("floor_price")
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw) / 1e9 if raw > 1e6 else float(raw)
        return None

    async def _fetch_tonapi_listings(self) -> list[Lot] | None:
        """Поток листингов TonAPI в этой сборке не используется: публичного
        эндпоинта «новых листингов подарков» нет, поэтому честно уходим в mock.
        """
        return None


__all__ = [
    "GIFT_PRESETS",
    "GIFT_NAMES",
    "Lot",
    "MarketAPI",
    "getgems_collection_url",
    "getgems_item_url",
    "fragment_model_image",
]
