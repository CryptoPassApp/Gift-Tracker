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
import base64
import hashlib
import json
import logging
import random
import re
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from bot.config import Settings

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Пресеты 8 подарков (ЧАСТЬ 2 ТЗ) на РЕАЛЬНЫХ ассетах подарков Telegram.
# Имена подарков — канонические из Telegram Gift Dataset (Genie Lamp,
# Ion Gem, ...); выдуманных «названий коллекций» здесь нет.
#   * image         — обложка коллекции (CDN Fragment, image/webp);
#   * address       — адрес коллекции в TON из метаданных TonAPI;
#   * fragment_slug — слаг для per-model рендеров
#                     https://nft.fragment.com/gift/{slug}-{номер}.webp —
#                     канонические картинки КАЖДОГО конкретного NFT с его
#                     узором/фоном (те же ассеты показывает Getgems);
#   * models_path   — цены флора по каждой модели (атрибут Model) из
#                     датасета: models/{short_name}/prices.json;
#   * supply        — проверенный диапазон существующих номеров.
# Все URL проверены запросами (HTTP 200, image/webp); base_floor — живой
# флор коллекции из датасета на 2026-10-27 (mock-режим стартует от него).
# ----------------------------------------------------------------------
GIFT_PRESETS: dict[str, dict[str, Any]] = {
    "Plush Pepe": {
        "base_floor": 6470.0,
        "asset": "Plush Pepes",
        "image": "https://nft.fragment.com/collection/plushpepe.webp",
        # Реальная коллекция Getgems (Plush Pepes), проверено через TonAPI.
        "address": "0:46fa0e9a864014196a5e7d66f1f83ffdb10f2859bbf2ea9baeabbf14d9ce0d50",
        "address_verified": True,
        "dataset_name": "Plush Pepe",
        "models_path": "models/plush_pepe/prices.json",
        "fragment_slug": "plushpepe", "supply": 2024,
    },
    "Genie Lamp": {
        "base_floor": 38.0,
        # Обложка коллекции, как у остальных подарков (проверено: HTTP 200,
        # image/webp). Раньше здесь стоял рендер одного лота genielamp-1.
        "image": "https://nft.fragment.com/collection/genielamp.webp",
        "address": None, "address_verified": False,
        "dataset_name": "Genie Lamp",
        "models_path": "models/genie_lamp/prices.json",
        "fragment_slug": "genielamp", "supply": 4999,
    },
    "Ion Gem": {
        "base_floor": 74.0,
        "asset": "Ion Gems",
        "image": "https://nft.fragment.com/collection/iongem.webp",
        # Реальная коллекция Ion Gems, проверено через TonAPI.
        "address": "0:cbec731b71ad05b9fae31c5711a222e4122397917a0efef1f681550df09369e2",
        "address_verified": True,
        "dataset_name": "Ion Gem",
        "models_path": "models/ion_gem/prices.json",
        "fragment_slug": "iongem", "supply": 3000,
    },
    "Trapped Heart": {
        "base_floor": 17.6,
        "image": "https://nft.fragment.com/collection/trappedheart.webp",
        "address": None, "address_verified": False,
        "dataset_name": "Trapped Heart",
        "models_path": "models/trapped_heart/prices.json",
        "fragment_slug": "trappedheart", "supply": 4999,
    },
    "Astral Shard": {
        "base_floor": 120.0,
        "image": "https://nft.fragment.com/collection/astralshard.webp",
        "address": None, "address_verified": False,
        "dataset_name": "Astral Shard",
        "models_path": "models/astral_shard/prices.json",
        "fragment_slug": "astralshard", "supply": 4999,
    },
    "Heroic Helmet": {
        "base_floor": 188.88,
        "image": "https://nft.fragment.com/collection/heroichelmet.webp",
        "address": None, "address_verified": False,
        "dataset_name": "Heroic Helmet",
        "models_path": "models/heroic_helmet/prices.json",
        "fragment_slug": "heroichelmet", "supply": 3000,
    },
    "Mousse Cake": {
        "base_floor": 5.3,
        "image": "https://nft.fragment.com/collection/moussecake.webp",
        "address": None, "address_verified": False,
        "dataset_name": "Mousse Cake",
        "models_path": "models/mousse_cake/prices.json",
        "fragment_slug": "moussecake", "supply": 4999,
    },
    "Perfume Bottle": {
        "base_floor": 84.0,
        "image": "https://nft.fragment.com/collection/perfumebottle.webp",
        "address": None, "address_verified": False,
        "dataset_name": "Perfume Bottle",
        "models_path": "models/perfume_bottle/prices.json",
        "fragment_slug": "perfumebottle", "supply": 3000,
    },
}

EXTRA_GIFT_COLLECTIONS: dict[str, dict[str, Any]] = {
    "Resistance Dog": {
        "base_floor": 465.0,
        "asset": "Resistance Dogs",
        "image": "https://nft.fragment.com/collection/resistancedog.webp",
        "address": "0:075827023de994693139142aec3240f4ecb7582258ad209aa2f8860d1e574a12",
        "address_verified": True,
        "dataset_name": "Resistance Dog",
        "models_path": "models/resistance_dog/prices.json",
        "fragment_slug": "resistancedog", "supply": 20000,
    },
    "Mirage Lamp": {
        "base_floor": 4.08,
        "asset": "Mirage Lamps",
        "image": "https://nft.fragment.com/collection/miragelamp.webp",
        "address": "0:3d3a771621bf604600fb2c397f25ce44df8471e03786532403f8b4784cd2844a",
        "address_verified": True,
        "dataset_name": "Mirage Lamp",
        "models_path": "models/mirage_lamp/prices.json",
        "fragment_slug": "miragelamp", "supply": 50000,
    },
    "Fine Pen": {
        "base_floor": 8.44,
        "asset": "Fine Pens",
        "image": "https://nft.fragment.com/collection/finepen.webp",
        "address": "0:42c059da94dd1a1b3a098158a1b4c2d3797b8a3bfb47231abb6dd74d9671f626",
        "address_verified": True,
        "dataset_name": "Fine Pen",
        "models_path": "models/fine_pen/prices.json",
        "fragment_slug": "finepen", "supply": 25000,
    },
}

# Канонические имена подарков, которые раньше были выдуманными алиасами
# (Magic Hat, Diamond Box, ...) — нужны для миграции старых записей в БД.
LEGACY_GIFT_ALIASES: dict[str, str] = {
    "Magic Hat": "Genie Lamp",
    "Diamond Box": "Ion Gem",
    "Heart Box": "Trapped Heart",
    "Golden Star": "Astral Shard",
    "Spaceman": "Heroic Helmet",
    "Birthday Cake": "Mousse Cake",
    "Champagne": "Perfume Bottle",
}

GIFT_NAMES: list[str] = list(GIFT_PRESETS.keys())

# Источники, где флоры и цены по моделям берутся из публичного датасета.
_DATASET_SOURCES = {"giftdataset", "real", "getgems"}


def fragment_model_image(gift_name: str, model_number: int, slug: str | None = None) -> str:
    """Картинка конкретной модели подарка (узор/фон этого NFT).

    Fragment хостит канонические рендеры каждого номера:
    https://nft.fragment.com/gift/{slug}-{номер}.webp — именно их
    отображает Getgems на странице лота. Для подарков вне пресетов
    slug выводится из имени.
    """
    preset = GIFT_PRESETS.get(gift_name) or {}
    resolved = slug or preset.get("fragment_slug") or gift_name.lower().replace(" ", "")
    return f"https://nft.fragment.com/gift/{resolved}-{model_number}.webp"


def friendly_address(raw: str | None) -> str:
    """raw '0:<hex>' -> user-friendly 'EQ...' (base64url + crc16-xmodem).

    Если адрес уже в friendly-формате (EQ/UQ) или не валиден как raw-адрес,
    возвращает строку без изменений.
    """
    if not raw or not isinstance(raw, str):
        return "" if raw is None else str(raw)
    raw = raw.strip()
    if ":" not in raw:
        return raw
    try:
        wc_str, hex_part = raw.split(":", 1)
        wc = int(wc_str)
        if wc not in (0, -1) or len(hex_part) != 64:
            return raw
        payload = bytes([0x11]) + (b"\xff" if wc == -1 else b"\x00") + bytes.fromhex(hex_part)
        crc = 0
        for b in payload:
            crc ^= b << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return base64.urlsafe_b64encode(payload + crc.to_bytes(2, "big")).decode().rstrip("=")
    except Exception:
        return raw


def getgems_collection_url(gift_name: str) -> str:
    """Живая ссылка на коллекцию подарка в Getgems (или поиск по имени).

    Ведём на адрес только если он проверен: непроверенные адреса ведут на
    чужие коллекции-однодневки, поэтому для них используем поиск.
    """
    preset = GIFT_PRESETS.get(gift_name)
    address = preset.get("address") if preset else None
    if address and preset.get("address_verified"):
        return f"https://getgems.io/collection/{friendly_address(address)}"
    return "https://getgems.io/search?text=" + quote(gift_name)


def getgems_item_url(item_address: str) -> str:
    """Живая ссылка на конкретный NFT-лот в Getgems."""
    return f"https://getgems.io/nft/{friendly_address(item_address)}"


# Маркеты, у которых нет публичной веб-страницы лота: покупка идет
# через их официальный Mini App (проверено: t.me-страницы открываются, HTTP 200).
PORTALS_MARKET_URL = "https://t.me/portals/market?startapp=to_market"
MRKT_MINIAPP_URL = "https://t.me/mrkt/app"
TONNEL_MARKET_URL = "https://t.me/tonnel_network_bot/gift"

# Deep-link на КОНКРЕТНЫЙ гифт в Portals. Формат подсмотрен в их собственном
# share-генераторе (app.portals.tg/assets/index-*.js): formatShareLink строит
# `${publicBotUrl}?startapp=gift_${item.id}`, где publicBotUrl =
# https://t.me/portals_market_bot/market. Открывает Mini App сразу на лоте.
PORTALS_ITEM_URL = "https://t.me/portals_market_bot/market?startapp=gift_{id}"

# MRKT: REST-апи их Mini App (доступно без Cloudflare, нужна сессия по initData).
MRKT_API_BASE = "https://api.tgmrkt.io"


def mrkt_order_url(order_id: str) -> str:
    """Ссылка на ордер-автобай в MRKT Mini App (как в share-ссылках GiftParser)."""
    return f"https://t.me/mrkt/app?startapp={str(order_id).replace('-', '')}"


def tonnel_lot_url(item_id: str | int | None = None) -> str:
    """Ссылка на лот Tonnel Network (официальный Mini App).

    Если передан ID лота — открывает конкретный подарок через ?startapp={id},
    иначе ведет на главную страницу подарков в Tonnel.
    """
    sid = str(item_id or "").strip()
    return f"{TONNEL_MARKET_URL}?startapp={sid}" if sid else TONNEL_MARKET_URL


def mrkt_lot_url(item: dict[str, Any]) -> str:
    """Ссылка на лот MRKT (TGMrkt).

    Веб-версия mrkt.io закрыта Cloudflare (403 без браузера), поэтому
    если API не отдал прямую ссылку — ведем в официальный Mini App.
    """
    url = str(item.get("url") or "").strip()
    return url if url.startswith(("http://", "https://")) else MRKT_MINIAPP_URL


def extract_traits(metadata: dict[str, Any]) -> dict[str, str | None]:
    """Достает атрибуты конкретного NFT (Model/Backdrop/Symbol) из метаданных.

    TonAPI отдает их в ``metadata.attributes``: у Plush Pepe это, например,
    Model=Barcelona, Backdrop=Lemongrass, Symbol=Cap. Именно эти значения
    отличают один лот от другого — без них все карточки «на одно лицо».
    """
    traits: dict[str, str | None] = {"model": None, "backdrop": None, "symbol": None}
    for attr in metadata.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        key = str(attr.get("trait_type") or "").strip().lower()
        value = attr.get("value")
        if key in traits and isinstance(value, str) and value.strip():
            traits[key] = value.strip()
    return traits


def normalize_collection_name(name: str) -> str:
    """Нормализует имя для сопоставления: регистр, пунктуация, пробелы."""
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def name_variants(name: str) -> set[str]:
    """Варианты имени подарка: реестр коллекций использует множественное число.

    Telegram-подарок «Plush Pepe», коллекция — «Plush Pepes»; сравнение
    делается по пересечению вариантов (с «s» и без).
    """
    base = normalize_collection_name(name)
    if not base:
        return set()
    variants = {base}
    if base.endswith("s"):
        variants.add(base[:-1])
    variants.add(base + "s")
    return variants


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
    # Атрибуты конкретного NFT из метаданных (TonAPI: Model/Backdrop/Symbol).
    model: str | None = None
    backdrop: str | None = None
    symbol: str | None = None


@dataclass
class TraitPrices:
    """Флоры по атрибутам одной коллекции из датасета TelegramGifts.

    ``models`` — цена флора для каждой модели (Model), ``backdrops`` и
    ``symbols`` — для фонов и символов. Источник обновляется независимо
    от нашего сканера, поэтому данные кэшируются по TTL.
    """

    models: dict[str, float] = field(default_factory=dict)
    backdrops: dict[str, float] = field(default_factory=dict)
    symbols: dict[str, float] = field(default_factory=dict)
    fetched_at: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not (self.models or self.backdrops or self.symbols)


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
        # Реальный поток Getgems: кэш лотов, курсоры страниц и выборка цен
        # для reference-floor (медиана текущих листингов).
        self._tonapi_listings_cache: list[Lot] | None = None
        self._tonapi_listings_cached_at = 0.0
        self._tonapi_cursors: dict[str, int] = {}
        self._price_samples: dict[str, deque[float]] = {}
        # Полный каталог подарков из датасета (все NFT, не только 8 пресетов).
        self._catalog: dict[str, dict[str, Any]] = {}
        # Флоры по атрибутам (Model/Backdrop/Symbol) на коллекцию.
        self._trait_prices: dict[str, TraitPrices] = {}
        # TonAPI: троттлинг запросов и пауза после 429 (анонимный лимит — сутки).
        self._tonapi_next_request_at: float = 0.0
        self._tonapi_cooldown_until: float = 0.0
        # Адреса коллекций из реестра Tonkeeper и курсор обхода коллекций.
        self._collection_index: dict[str, dict[str, Any]] = {}
        self._collection_index_updated_at: float = 0.0
        self._collection_index_retry_at: float = 0.0
        self._collection_cursor: int = 0
        # Portals: листинги и флоры (публичный API Mini App, без ключа).
        self._portals_cache: list[Lot] | None = None
        self._portals_cached_at: float = 0.0
        self._portals_floors: dict[str, float] = {}
        self._portals_floors_at: float = 0.0
        # Картинки моделей из листингов Portals: "Gift|model" -> photo_url.
        self._portal_model_images: dict[str, str] = {}
        # Официальные рендеры моделей из датасета (models/<gift>/config.json):
        # gift -> {model_casefold: webp_url}. Негативный результат кэшируется {}.
        self._dataset_model_images: dict[str, dict[str, str]] = {}
        # Маркет-ордера MRKT (автобай): кэш на MRKT_ORDERS_TTL_SEC + флаг,
        # чтобы «токен протух» не спамил лог каждый скан.
        self._mrkt_orders_cache: list[dict[str, Any]] | None = None
        self._mrkt_orders_at: float = 0.0
        self._mrkt_token_warned: bool = False
        # Живой курс TON/USD из TonAPI
        self._ton_usd_rate: float = settings.TON_USD_RATE
        self._ton_usd_rate_cached_at: float = 0.0
        self._load_collection_index_cache()

    async def fetch_ton_usd_rate(self) -> float:
        """Получает живой курс TON/USD из TonAPI (кэш 5 минут, fallback из настроек)."""
        now = time.monotonic()
        if self._ton_usd_rate_cached_at and (now - self._ton_usd_rate_cached_at) < 300:
            return self._ton_usd_rate
        headers = {"User-Agent": "GiftTracker/1.0", "Accept": "application/json"}
        if self.settings.TONAPI_KEY:
            headers["Authorization"] = f"Bearer {self.settings.TONAPI_KEY}"
        try:
            async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
                resp = await client.get("https://tonapi.io/v2/rates?tokens=ton&currencies=usd")
                if resp.status_code == 200:
                    val = (
                        resp.json()
                        .get("rates", {})
                        .get("TON", {})
                        .get("prices", {})
                        .get("USD")
                    )
                    if isinstance(val, (int, float)) and val > 0:
                        self._ton_usd_rate = round(float(val), 2)
                        self._ton_usd_rate_cached_at = now
                        self.settings.TON_USD_RATE = self._ton_usd_rate
                        return self._ton_usd_rate
        except Exception as exc:
            logger.debug("TON/USD rate fetch failed (%s) — using fallback", exc)
        return self._ton_usd_rate or self.settings.TON_USD_RATE

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------
    async def get_floors(self) -> dict[str, dict[str, Any]]:
        """Текущие флоры коллекций (реальные или mock, в зависимости от MARKET_SOURCE)."""
        async with self._lock:
            now = time.monotonic()
            source = (self.settings.MARKET_SOURCE or "mock").strip().lower()
            ttl = (
                self.settings.GIFTDATASET_TTL_SEC
                if source == "giftdataset"
                else self._cache_ttl
            )
            if self._floors_cache is not None and (now - self._floors_cached_at) < ttl:
                return self._floors_cache

            floors: dict[str, dict[str, Any]] | None = None
            if source == "giftdataset":
                floors = await self._fetch_giftdataset_floors()
                if floors is not None:
                    # Адреса коллекций — из публичного реестра (раз в неделю),
                    # чтобы сканер видел лоты не только двух коллекций.
                    try:
                        await self.refresh_collection_index()
                    except Exception:  # noqa: BLE001 — реестр не должен ломать флоры
                        logger.exception("Collection registry refresh failed")
                    try:
                        # Живой флор Portals: по нему оцениваются его лоты.
                        await self._merge_portals_floors(floors)
                    except Exception:  # noqa: BLE001 — Portals не должен ломать флоры
                        logger.exception("Portals floors merge failed")
                if floors is None:
                    # Реальные данные не подменяем демо: лучше отдать прошлый
                    # снимок или пусто, чем показывать выдуманные цены.
                    floors = self._floors_cache
            elif source == "tonapi":
                floors = await self._fetch_tonapi_floors()
                if floors is None:
                    floors = self._floors_cache
            elif source == "mock":
                floors = self._mock_floors()

            if floors is None:
                floors = {}

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
        source = (self.settings.MARKET_SOURCE or "mock").strip().lower()

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

        # Portals: публичный API Mini App, ключ не нужен.
        if "portals" in enabled:
            lots.extend(await self._fetch_portals_listings())

        if source in {"giftdataset", "real", "getgems"}:
            # Реальный поток: sale-лоты Getgems через TonAPI (без токенов).
            lots.extend(await self._fetch_tonapi_listings())
        else:
            # Демо-поток: только когда MARKET_SOURCE=mock.
            lots.extend(self._mock_listings(source="getgems"))
        return lots

    # ------------------------------------------------------------------
    # Реальные данные без токенов
    # ------------------------------------------------------------------
    async def _fetch_giftdataset_floors(self) -> dict[str, dict[str, Any]] | None:
        """Флоры TGMrkt/Getgems/Portals из публичного датасета TelegramGifts."""
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": "GiftTracker/1.0"}
            ) as client:
                resp = await client.get(self.settings.GIFTDATASET_URL)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GiftDataset floors fetch failed (%s) — keeping last snapshot", exc)
            return None
        floors = self._parse_giftdataset_floors(payload)
        if floors:
            logger.info("GiftDataset floors loaded: %d collection(s)", len(floors))
        return floors

    def _parse_giftdataset_floors(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
        """Парсит каталог NFT-подарков из официального датасета TelegramGifts.

        В датасете 123 upgraded-подарка; первые 8 сопоставляются
        с display-пресетами бота по имени, остальные наследуют
        картинку/fragment-slug/модели автоматически.
        """
        presets_by_dataset: dict[str, tuple[str, dict[str, Any]]] = {}
        for preset_name, preset in GIFT_PRESETS.items():
            key = str(preset.get("dataset_name") or preset_name).strip().lower()
            presets_by_dataset[key] = (preset_name, preset)

        catalog: dict[str, dict[str, Any]] = {}
        for entry in payload.get("upgraded") or []:
            if not isinstance(entry, dict):
                continue
            full_name = str(entry.get("full_name") or "").strip()
            if not full_name:
                continue
            known = presets_by_dataset.get(full_name.lower())
            preset_name, preset = known if known else (full_name, {})
            if preset_name in catalog:
                continue

            prices: dict[str, float] = {}
            for field_name, label in (
                ("getgems_price_ton", "getgems"),
                ("tgmrkt_price_ton", "tgmrkt"),
                ("portal_price_ton", "portals"),
                ("floor_price_ton", "floor"),
            ):
                value = entry.get(field_name)
                if isinstance(value, (int, float)) and value > 0:
                    prices[label] = round(float(value), 2)
            if not prices:
                continue

            short_name = str(entry.get("short_name") or "").strip()
            regular_id = str(entry.get("regular_id") or "").strip()
            image = preset.get("image")
            if not image and regular_id:
                # Fallback на официальный assets-репозиторий.
                image = (
                    "https://raw.githubusercontent.com/ssamy2/TelegramGiftsAssests/"
                    f"main/webp/by_id/{regular_id}.webp"
                )
            address = preset.get("address") if preset.get("address_verified") else None
            if not address:
                # Подтягиваем верифицированный адрес Tonkeeper (из data/gift_collections.json).
                resolved = self._collection_index.get(preset_name)
                if resolved:
                    address = resolved.get("address")
            models_path = str(entry.get("models") or "").strip()
            if not models_path:
                models_path = f"models/{short_name}/prices.json"
            catalog[preset_name] = {
                "name": preset_name,
                "asset": preset.get("asset") or full_name,
                "image": image,
                "address": address,
                "address_verified": bool(address),
                "dataset_name": full_name,
                "short_name": short_name,
                "regular_id": regular_id,
                "fragment_slug": preset.get("fragment_slug") or short_name.replace("_", ""),
                "models_path": models_path,
                "base_floor": min(prices.values()),
                "floor": min(prices.values()),
                # Исторический тренд не отдаём: фейк 24ч-дельты
                # в API ломает floor_snapshots (None = нет замера).
                "change_24h": None,
                "liquidity": _liquidity_for(preset_name),
                "market_floors": prices,
            }

        if not catalog:
            return None

        # Добавляем верифицированные новые коллекции (Resistance Dog, Mirage Lamp, Fine Pen)
        for extra_name, preset in EXTRA_GIFT_COLLECTIONS.items():
            if extra_name not in catalog:
                bf = float(preset.get("base_floor", 10.0))
                prices = {"floor": bf, "portals": bf, "getgems": bf}
                catalog[extra_name] = {
                    "name": extra_name,
                    "asset": preset.get("asset") or extra_name,
                    "image": preset.get("image"),
                    "address": preset.get("address"),
                    "address_verified": bool(preset.get("address_verified")),
                    "dataset_name": extra_name,
                    "short_name": preset.get("fragment_slug") or extra_name.lower().replace(" ", "_"),
                    "regular_id": "",
                    "fragment_slug": preset.get("fragment_slug") or extra_name.lower().replace(" ", ""),
                    "models_path": preset.get("models_path") or f"models/{extra_name.lower().replace(' ', '_')}/prices.json",
                    "base_floor": bf,
                    "floor": bf,
                    "change_24h": None,
                    "liquidity": _liquidity_for(extra_name),
                    "market_floors": prices,
                }

        self._catalog = catalog
        return {
            name: {
                "floor": info["floor"],
                "change_24h": info["change_24h"],
                "liquidity": info["liquidity"],
                "image": info["image"],
                "address": info["address"],
                "market_floors": info["market_floors"],
            }
            for name, info in catalog.items()
        }

    def get_catalog(self) -> dict[str, dict[str, Any]]:
        """Полный каталог NFT-подарков (пусто, пока датасет не загружен)."""
        return self._catalog

    # ------------------------------------------------------------------
    # Адреса коллекций: публичный реестр Tonkeeper (без ключей и лимитов)
    # ------------------------------------------------------------------
    def _gift_names(self) -> set[str]:
        return {name for name in (set(GIFT_PRESETS) | set(EXTRA_GIFT_COLLECTIONS) | set(self._catalog)) if name}

    def _load_collection_index_cache(self) -> None:
        """Читает кэш адресов с диска (переживает рестарт и экономит запросы)."""
        path = Path(self.settings.GIFT_COLLECTIONS_FILE)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        gifts = payload.get("gifts") if isinstance(payload, dict) else None
        if not isinstance(gifts, dict):
            return
        for gift, info in gifts.items():
            if isinstance(info, dict) and str(info.get("address") or "").startswith("0:"):
                self._collection_index[str(gift)] = {
                    "address": str(info["address"]),
                    "name": str(info.get("name") or ""),
                }
        try:
            self._collection_index_updated_at = float(payload.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            self._collection_index_updated_at = 0.0
        if self._collection_index:
            logger.info("Collection addresses from cache: %d gift(s)", len(self._collection_index))

    def _save_collection_index_cache(self) -> None:
        path = Path(self.settings.GIFT_COLLECTIONS_FILE)
        payload = {
            "updated_at": self._collection_index_updated_at,
            "source": self.settings.GIFT_COLLECTIONS_URL,
            "gifts": self._collection_index,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("Collection address cache write failed (%s)", exc)

    def _match_registry(self, payload: Any) -> dict[str, dict[str, str]]:
        """Сопоставляет записи реестра (address+name) с именами подарков."""
        targets: dict[str, str] = {}
        for gift in self._gift_names():
            for variant in name_variants(gift):
                targets.setdefault(variant, gift)

        found: dict[str, dict[str, str]] = {}
        entries = payload if isinstance(payload, list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            address = str(entry.get("address") or "").strip()
            if not name or not address.startswith("0:") or len(address) != 66:
                continue
            gift = next(
                (targets[variant] for variant in name_variants(name) if variant in targets),
                None,
            )
            if gift is None:
                continue
            found[gift] = {"address": address, "name": name}
        return found

    async def refresh_collection_index(self, *, force: bool = False) -> int:
        """Обновляет адреса коллекций из публичного реестра (кэш на неделю).

        Реестр Tonkeeper отдает ``[{address, name}]`` — 115 из 123 подарков.
        Именно поэтому лоты перестают ограничиваться двумя коллекциями.
        """
        now = time.time()
        if not force and (now - self._collection_index_updated_at) < self.settings.COLLECTION_INDEX_TTL_SEC:
            return 0
        if not force and time.monotonic() < self._collection_index_retry_at:
            return 0
        self._collection_index_retry_at = time.monotonic() + self.settings.COLLECTION_INDEX_RETRY_SEC

        headers = {"User-Agent": "GiftTracker/1.0", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
                resp = await client.get(self.settings.GIFT_COLLECTIONS_URL)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Collection registry fetch failed (%s) — cached addresses stay", exc)
            return 0

        found = self._match_registry(payload)
        added = 0
        for gift, info in found.items():
            current = self._collection_index.get(gift)
            if current and current.get("address") == info["address"]:
                continue
            self._collection_index[gift] = info
            added += 1

        self._collection_index_updated_at = time.time()
        self._save_collection_index_cache()
        self._apply_collection_addresses()
        logger.info(
            "Collection registry: %d gift(s) matched, %d new/changed, %d known in total",
            len(found),
            added,
            len(self._collection_index),
        )
        return added

    def _apply_collection_addresses(self) -> None:
        """Проставляет найденные адреса в каталог (для сканера и ссылок Getgems)."""
        for name, info in self._catalog.items():
            if info.get("address"):
                continue
            resolved = self._collection_index.get(name)
            if resolved:
                info["address"] = resolved["address"]
                info["address_verified"] = True

    # ------------------------------------------------------------------
    # Portals (portals.tg) — публичный API Mini App, без ключа
    # ------------------------------------------------------------------
    @staticmethod
    def _portal_slug(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(name).lower())

    def _gift_by_name(self, name: str) -> str | None:
        """Сопоставляет имя подарка из Portals с ключом каталога."""
        targets: dict[str, str] = {}
        for gift in self._gift_names():
            for variant in name_variants(gift):
                targets.setdefault(variant, gift)
        return next((targets[v] for v in name_variants(name) if v in targets), None)

    def _gift_by_slug(self, slug: str) -> str | None:
        key = self._portal_slug(slug)
        for gift in self._gift_names():
            if self._portal_slug(gift) in (key, key.rstrip("s")):
                return gift
        return None

    async def _fetch_portals_floors(self) -> dict[str, float]:
        """Флоры коллекций из Portals (123 записи, ключ не нужен)."""
        now = time.monotonic()
        if self._portals_floors and (now - self._portals_floors_at) < self.settings.PORTALS_FLOORS_TTL_SEC:
            return self._portals_floors
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": "GiftTracker/1.0"}
            ) as client:
                resp = await client.get(f"{self.settings.PORTALS_API_BASE}/collections/floors")
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Portals floors fetch failed (%s)", exc)
            return self._portals_floors

        result: dict[str, float] = {}
        for slug, raw in (payload.get("floorPrices") or {}).items():
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            gift = self._gift_by_slug(str(slug))
            if gift:
                result[gift] = round(value, 2)
        self._portals_floors = result
        self._portals_floors_at = now
        logger.info("Portals floors: %d collection(s)", len(result))
        return result

    async def _merge_portals_floors(self, floors: dict[str, dict[str, Any]]) -> None:
        """Добавляет флор Portals в ``market_floors`` — по нему считаются его лоты."""
        portals = await self._fetch_portals_floors()
        for gift_name, floor in portals.items():
            info = floors.get(gift_name)
            if not info:
                continue
            market_floors = dict(info.get("market_floors") or {})
            market_floors["portals"] = round(float(floor), 2)
            info["market_floors"] = market_floors

    def _parse_portals_items(self, items: list[dict[str, Any]]) -> list[Lot]:
        """Листинги Portals -> Lot (цена, номер, модель/фон/символ, картинка)."""
        lots: list[Lot] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") != "listed":
                continue
            try:
                price = float(item.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            gift = self._gift_by_name(str(item.get("name") or ""))
            item_id = str(item.get("id") or "")
            if gift is None or not item_id:
                continue
            number = item.get("external_collection_number")
            model_number = int(number) if isinstance(number, int) else None
            attrs = {
                str(attr.get("type")): attr.get("value")
                for attr in (item.get("attributes") or [])
                if isinstance(attr, dict)
            }
            model = str(attrs["model"]) if attrs.get("model") else None
            photo = item.get("photo_url") or None
            # Запоминаем рендер модели — пикер калькулятора показывает его в списке.
            if model and photo:
                self._portal_model_images[f"{gift}|{model.lower()}"] = str(photo)
            lots.append(
                Lot(
                    gift_name=gift,
                    lot_number=f"#{model_number}" if model_number else "#----",
                    price=round(price, 2),
                    # У Portals нет веб-страницы лота: открываем их Mini App
                    # сразу на этом гифте (deep-link их собственного
                    # share-генератора, ?startapp=gift_<id>).
                    marketplace_url=PORTALS_ITEM_URL.format(id=item_id),
                    image_url=photo,
                    model_number=model_number,
                    source="portals",
                    model=model,
                    backdrop=str(attrs["backdrop"]) if attrs.get("backdrop") else None,
                    symbol=str(attrs["symbol"]) if attrs.get("symbol") else None,
                )
            )
        return lots

    def portal_model_image(self, gift_name: str, model: str) -> str | None:
        """Рендер модели из листингов Portals (если такой лот уже встречался)."""
        return self._portal_model_images.get(f"{gift_name}|{str(model).lower()}")

    async def dataset_model_images(self, gift_name: str) -> dict[str, str]:
        """Официальные рендеры моделей из датасета (``models/<gift>/config.json``).

        Датасет хранит webp-ассет каждой модели Telegram рядом с ценами
        (``webp_path``). Имена в конфиге snake_case, в prices.json — с
        пробелами, поэтому ключи нормализуем (пробелы + casefold). Итог
        кэшируется на жизнь процесса, включая негативный результат.
        """
        cached = self._dataset_model_images.get(gift_name)
        if cached is not None:
            return cached

        result: dict[str, str] = {}
        prices_path = self._models_path(gift_name)
        if prices_path and prices_path.endswith("prices.json"):
            cfg_path = prices_path.replace("prices.json", "config.json")
            url = (
                f"{self.settings.GIFTDATASET_BASE_URL.rstrip('/')}/{cfg_path.lstrip('/')}"
            )
            try:
                async with httpx.AsyncClient(
                    timeout=15.0, headers={"User-Agent": "GiftTracker/1.0"}
                ) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    payload = resp.json()
                base = self.settings.GIFTDATASET_BASE_URL.rstrip("/")
                for entry in payload if isinstance(payload, list) else []:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("name") or "").replace("_", " ").strip().casefold()
                    webp = str(entry.get("webp_path") or "").strip()
                    if name and webp:
                        result[name] = f"{base}/{webp.lstrip('/')}"
            except (httpx.HTTPError, ValueError):
                logger.warning("Dataset model images fetch failed for %s", gift_name)

        self._dataset_model_images[gift_name] = result
        return result

    async def _fetch_portals_listings(self) -> list[Lot]:
        """Свежие листинги Portals (до 50 за запрос, кэш на TTL)."""
        now = time.monotonic()
        if (
            self._portals_cache is not None
            and (now - self._portals_cached_at) < self.settings.PORTALS_LISTINGS_TTL_SEC
        ):
            return self._portals_cache
        # Кэш ставим до запроса: после 429 следующая попытка — через TTL.
        self._portals_cached_at = now
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": "GiftTracker/1.0"}
            ) as client:
                resp = await client.get(
                    f"{self.settings.PORTALS_API_BASE}/nfts/search",
                    params={"limit": self.settings.PORTALS_LISTINGS_LIMIT},
                )
                if resp.status_code == 429:
                    logger.warning(
                        "Portals rate limit (429) — retry after %.0fs",
                        self.settings.PORTALS_LISTINGS_TTL_SEC,
                    )
                    return self._portals_cache or []
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Portals listings fetch failed (%s)", exc)
            return self._portals_cache or []
        lots = self._parse_portals_items(payload.get("results") or [])
        self._portals_cache = lots
        logger.info("Portals listings: %d sale lot(s)", len(lots))
        return lots

    def _scan_collections(self) -> list[tuple[str, str]]:
        """Коллекции текущего прохода: курсор двигается по кругу.

        Порядок — по флору, от дешёвых к дорогим: при жёстком лимите TonAPI
        пользователь сначала увидит лоты ширпотреба (5–80 TON), а не только
        Plush Pepe за 6470 TON.
        """
        entries = [
            (name, str(info["address"]), float(info.get("floor") or 0.0))
            for name, info in self._catalog.items()
            if info.get("address") and info.get("address_verified")
        ]
        if not entries:
            return []
        entries.sort(key=lambda item: item[2])
        start = self._collection_cursor % len(entries)
        count = min(self.settings.TONAPI_COLLECTIONS_PER_SCAN, len(entries))
        selected = [entries[(start + index) % len(entries)] for index in range(count)]
        self._collection_cursor = (start + count) % len(entries)
        return [(name, address) for name, address, _ in selected]

    async def _tonapi_wait(self) -> bool:
        """Выдерживает интервал между запросами; False — действует пауза после 429."""
        now = time.monotonic()
        if now < self._tonapi_cooldown_until:
            return False
        delay = self._tonapi_next_request_at - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._tonapi_next_request_at = time.monotonic() + self.settings.TONAPI_MIN_REQUEST_INTERVAL_SEC
        return True

    async def _fetch_tonapi_listings(self) -> list[Lot]:
        """Sale-лоты Getgems через TonAPI: несколько коллекций за проход."""
        now = time.monotonic()
        if (
            self._tonapi_listings_cache is not None
            and (now - self._tonapi_listings_cached_at) < self.settings.TONAPI_LISTINGS_TTL_SEC
        ):
            return self._tonapi_listings_cache

        if not self._catalog:
            # Каталог строится вместе с флорами; если сюда пришли раньше —
            # подгружаем его, чтобы не потерять ни одной коллекции.
            await self.get_floors()

        selected = self._scan_collections()
        lots: list[Lot] = []
        headers = {"User-Agent": "GiftTracker/1.0", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            for name, address in selected:
                preset = self._catalog.get(name) or {}
                offset = int(self._tonapi_cursors.get(name, 0))
                for _ in range(self.settings.TONAPI_SCAN_PAGES):
                    items = await self._fetch_tonapi_page(client, address, offset)
                    if items is None:
                        break
                    if not items:
                        offset = 0
                        break
                    lots.extend(self._parse_tonapi_items(items, name, preset))
                    if len(items) < 100:
                        offset = 0
                        break
                    offset += 100
                self._tonapi_cursors[name] = offset

        ready = sum(
            1
            for info in self._catalog.values()
            if info.get("address_verified") and info.get("address")
        )
        cooldown = self.tonapi_cooldown_left
        logger.info(
            "TonAPI listings: %d sale lot(s) from %d/%d collection(s) this pass, %d with address%s",
            len(lots),
            len(selected),
            ready,
            ready,
            f", cooldown {cooldown:.0f}s" if cooldown > 0 else "",
        )
        self._tonapi_listings_cache = lots
        self._tonapi_listings_cached_at = now
        return lots

    @property
    def tonapi_cooldown_left(self) -> float:
        """Сколько секунд осталось до конца паузы после 429."""
        return max(0.0, self._tonapi_cooldown_until - time.monotonic())

    async def _fetch_tonapi_page(
        self, client: httpx.AsyncClient, address: str, offset: int
    ) -> list[dict[str, Any]] | None:
        """Страница айтемов коллекции с троттлингом и паузой после 429."""
        if not await self._tonapi_wait():
            return None
        url = f"{self.settings.TONAPI_BASE_URL}/nfts/collections/{address}/items"
        try:
            resp = await client.get(url, params={"limit": 100, "offset": offset})
        except httpx.HTTPError as exc:
            logger.warning("TonAPI items fetch failed for %s (%s)", address[:12], exc)
            return None
        if resp.status_code == 429:
            self._tonapi_cooldown_until = (
                time.monotonic() + self.settings.TONAPI_COOLDOWN_ON_429_SEC
            )
            logger.warning(
                "TonAPI rate limit (429) — market scans paused for %.0f min. "
                "Set TONAPI_KEY in .env for a higher limit.",
                self.settings.TONAPI_COOLDOWN_ON_429_SEC / 60,
            )
            return None
        if resp.status_code != 200:
            logger.warning(
                "TonAPI items HTTP %s for %s offset=%s", resp.status_code, address[:12], offset
            )
            return None
        try:
            return resp.json().get("nft_items") or []
        except ValueError:
            return None

    def _parse_tonapi_items(
        self, items: list[dict[str, Any]], gift_name: str, preset: dict[str, Any]
    ) -> list[Lot]:
        """Превращает sale-айтемы TonAPI в лоты; без sale — пропускает."""
        lots: list[Lot] = []
        samples = self._price_samples.setdefault(gift_name, deque(maxlen=200))
        for item in items:
            sale = item.get("sale") or {}
            price_info = sale.get("price") or {}
            try:
                price_ton = float(price_info.get("value")) / 1e9
            except (TypeError, ValueError):
                continue
            if price_ton <= 0:
                continue
            address = item.get("address")
            if not address:
                continue
            meta = item.get("metadata") or {}
            match = re.search(r"#(\d+)", str(meta.get("name") or ""))
            model_number = int(match.group(1)) if match else None
            traits = extract_traits(meta)
            clean_addr = friendly_address(address)
            samples.append(price_ton)
            lots.append(
                Lot(
                    gift_name=gift_name,
                    lot_number=f"#{model_number}" if model_number else "#----",
                    price=round(price_ton, 2),
                    marketplace_url=f"https://getgems.io/nft/{clean_addr}",
                    item_address=clean_addr,
                    image_url=(
                        fragment_model_image(
                            gift_name, model_number, preset.get("fragment_slug")
                        )
                        if model_number
                        else preset.get("image")
                    ),
                    model_number=model_number,
                    source="getgems",
                    model=traits["model"],
                    backdrop=traits["backdrop"],
                    symbol=traits["symbol"],
                )
            )
        if lots:
            valid_prices = [lot.price for lot in lots if lot.price > 0]
            if valid_prices:
                min_sale = min(valid_prices)
                if gift_name in self._catalog:
                    mf = dict(self._catalog[gift_name].get("market_floors") or {})
                    mf["getgems"] = round(min_sale, 2)
                    self._catalog[gift_name]["market_floors"] = mf
                if self._floors_cache and gift_name in self._floors_cache:
                    mf = dict(self._floors_cache[gift_name].get("market_floors") or {})
                    mf["getgems"] = round(min_sale, 2)
                    self._floors_cache[gift_name]["market_floors"] = mf
        return lots

    def reference_floor(self, gift_name: str, default: float) -> float:
        """Медиана текущих листингов как reference-floor для оценки дисконта."""
        samples = self._price_samples.get(gift_name)
        if samples and len(samples) >= 5:
            return round(statistics.median(samples), 2)
        return default

    # ------------------------------------------------------------------
    # MRKT: ордера-автобай (ставки покупателей) как EXIT-ликвидность
    # ------------------------------------------------------------------
    def _current_mrkt_token(self) -> str:
        """Живой токен MRKT: файл от сервиса автообновления, иначе .env.

        Сервис (scripts/mrkt_token_service.py) раз в 5 минут обновляет
        data/mrkt_token.json — сессионный токен MRKT живет ~8 минут, поэтому
        статичный токен из .env считается запасным вариантом.
        """
        try:
            blob = json.loads(Path(self.settings.MRKT_TOKEN_FILE).read_text(encoding="utf-8"))
            age = time.time() - float(blob.get("ts") or 0)
            token = str(blob.get("token") or "").strip()
            if token and 0 <= age < self.settings.MRKT_TOKEN_FILE_MAX_AGE_SEC:
                return token
        except (OSError, ValueError):
            pass
        return (self.settings.MRKT_AUTH_TOKEN or "").strip()

    async def _fetch_mrkt_orders(self) -> list[dict[str, Any]]:
        """Живые маркет-ордера MRKT (нужна сессия: MRKT_AUTH_TOKEN или сервис).

        Ордер — ставка покупателя: «куплю подарок коллекции X (модель/фон
        опционально) за до priceMaxNanoTONs». Кэш на MRKT_ORDERS_TTL_SEC;
        при 401/403/404 (токен протух) источник тихо отключается с разовым
        предупреждением в лог.
        """
        token = self._current_mrkt_token()
        if not token:
            return []
        now = time.monotonic()
        if (
            self._mrkt_orders_cache is not None
            and (now - self._mrkt_orders_at) < self.settings.MRKT_ORDERS_TTL_SEC
        ):
            return self._mrkt_orders_cache
        self._mrkt_orders_at = now
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={
                    # Полный браузерный UA обязателен: короткий "Mozilla/5.0 (...)"
                    # Cloudflare режет как бота, даже с валидным токеном.
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/json",
                    "Origin": "https://cdn.tgmrkt.io",
                    "Referer": "https://cdn.tgmrkt.io/",
                    "Authorization": token,
                },
            ) as client:
                resp = await client.post(
                    f"{MRKT_API_BASE}/api/v1/orders",
                    json={
                        "CollectionNames": [],
                        "ModelNames": [],
                        "BackdropNames": [],
                        "SymbolNames": [],
                        "cursor": "",
                        "count": 50,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("MRKT orders fetch failed (%s)", exc)
            return self._mrkt_orders_cache or []

        if resp.status_code in (401, 403, 404):
            # MRKT на просроченную сессию отвечает 404 (маршрут как бы «не
            # существует»), а не 401 — поэтому все три кода трактуем как
            # «токен протух, нужен свежий initData».
            if not self._mrkt_token_warned:
                logger.warning(
                    "MRKT token rejected (HTTP %s) — сессионный токен короткоживущий "
                    "(~минуты); нужен автопродлеваемый initData (MTProto) или ручное обновление",
                    resp.status_code,
                )
                self._mrkt_token_warned = True
            return []
        if resp.status_code != 200:
            logger.warning("MRKT orders HTTP %s", resp.status_code)
            return self._mrkt_orders_cache or []

        self._mrkt_token_warned = False
        orders = resp.json().get("orders") or []
        self._mrkt_orders_cache = orders
        logger.info("MRKT orders: %d bid(s)", len(orders))
        return orders

    async def _mrkt_top_bids(self) -> dict[str, dict[str, float]]:
        """Топ-бида автобая MRKT по нашим коллекциям: gift -> {price, order_id}.

        Ордера качает хук /api/internal/mrkt-init (API-процесс): токен MRKT
        живет секунды вне сессии минта, поэтому сканер в MRKT не ходит —
        он читает готовые бида из data/mrkt_bids.json (TTL 5 минут).
        """
        try:
            blob = json.loads(
                Path(self.settings.MRKT_BIDS_FILE).read_text(encoding="utf-8")
            )
            if time.time() - float(blob.get("ts") or 0) > 300:
                return {}
            orders = blob.get("orders") or []
        except (OSError, ValueError):
            return {}

        bids: dict[str, dict[str, float]] = {}
        for order in orders:
            gift = self._gift_by_name(str(order.get("collectionName") or ""))
            if not gift:
                continue
            try:
                price = float(order.get("priceMaxNanoTONs")) / 1e9
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            current = bids.get(gift)
            if current is None or price > current["price"]:
                bids[gift] = {"price": round(price, 2), "order_id": str(order.get("id") or "")}
        return bids

    # ------------------------------------------------------------------
    # Флоры по атрибутам: цена конкретной модели, а не всей коллекции
    # ------------------------------------------------------------------
    def _models_path(self, gift_name: str) -> str | None:
        """Путь к prices.json коллекции в датасете (None вне реальных источников)."""
        source = (self.settings.MARKET_SOURCE or "").strip().lower()
        if source not in _DATASET_SOURCES:
            return None
        entry = self._catalog.get(gift_name) or {}
        path = entry.get("models_path")
        if not path:
            preset = GIFT_PRESETS.get(gift_name) or {}
            path = preset.get("models_path")
        if path:
            return str(path)
        dataset_name = str(entry.get("dataset_name") or gift_name)
        return f"models/{dataset_name.lower().replace(' ', '_')}/prices.json"

    async def get_trait_prices(self, gift_name: str) -> TraitPrices | None:
        """Флоры по моделям/фонам/символам коллекции (кэш ``TRAIT_PRICES_TTL_SEC``).

        Возвращает ``None``, если датасет недоступен: вызывающий код тогда
        сравнивает лот с общим флором коллекции (прежнее поведение).
        """
        now = time.monotonic()
        cached = self._trait_prices.get(gift_name)
        if cached is not None and (now - cached.fetched_at) < self.settings.TRAIT_PRICES_TTL_SEC:
            return cached if not cached.is_empty else None

        path = self._models_path(gift_name)
        if path is None:
            return cached if cached is not None and not cached.is_empty else None

        url = f"{self.settings.GIFTDATASET_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": "GiftTracker/1.0"}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Trait prices fetch failed for %s (%s) — collection floor stays in use",
                gift_name,
                exc,
            )
            if cached is not None:
                cached.fetched_at = now  # не долбить сеть каждый скан, отдать прошлый снимок
                return cached if not cached.is_empty else None
            # Негативный кэш: не повторять запрос на каждом скане.
            self._trait_prices[gift_name] = TraitPrices(fetched_at=now)
            return None

        def _prices(key: str) -> dict[str, float]:
            raw = payload.get(key)
            if not isinstance(raw, dict):
                return {}
            return {
                str(name): round(float(value), 2)
                for name, value in raw.items()
                if isinstance(value, (int, float)) and value > 0
            }

        prices = TraitPrices(
            models=_prices("models"),
            backdrops=_prices("backdrops"),
            symbols=_prices("symbols"),
            fetched_at=now,
        )
        self._trait_prices[gift_name] = prices
        if prices.is_empty:
            logger.warning("Trait prices for %s are empty — collection floor stays in use", gift_name)
            return None
        logger.info(
            "Trait prices loaded for %s: %d model(s), %d backdrop(s), %d symbol(s)",
            gift_name,
            len(prices.models),
            len(prices.backdrops),
            len(prices.symbols),
        )
        return prices

    async def reference_price(
        self, lot: Lot, floors: dict[str, dict[str, Any]]
    ) -> tuple[float, str]:
        """Эталонная цена лота для поиска РЕАЛЬНЫХ выгодных предложений (снайпинга).

        Для арбитража/ликвидаций эталоном служит реальный ликвидный флор коллекции на
        маркетплейсе лота (или общий минимальный флор рынка).
        Любой лот с ценой >= флор коллекции НЕ является скидкой (он стоит по
        рыночной цене или с наценкой).

        Модель может повышать эталон ТОЛЬКО для проверенных коллекций
        с подтверждённым отдельным рынком (Plush Pepe Leonardo).
        Для остальных 120 коллекций цены моделей из датасета — это единичные
        неликвидные хотелки (например, Red Carpet 346 TON при реальном флоре 125 TON),
        которые вызывают ложные срабатывания и показывают фейковую выгоду.
        """
        info = floors.get(lot.gift_name) or {}
        market_floors = info.get("market_floors") or {}
        source_floor = market_floors.get(lot.source)
        base = float(source_floor or info.get("floor") or 0.0)
        if base <= 0:
            return 0.0, "unknown"

        reference, basis = base, "collection"

        # Проверенные модели с реальным отдельным рынком
        if lot.model and lot.gift_name == "Plush Pepe":
            prices = await self.get_trait_prices(lot.gift_name)
            if prices is not None:
                model_floor = prices.models.get(lot.model)
                if model_floor is not None and model_floor > base:
                    cap = base * self.settings.TRAIT_FLOOR_MAX_RATIO
                    if model_floor <= cap:
                        reference = round(float(model_floor), 2)
                        basis = f"model:{lot.model}"
                    else:
                        logger.warning(
                            "Model floor for %s/%s (%.1f TON) is %.1fx above collection floor — capped",
                            lot.gift_name,
                            lot.model,
                            model_floor,
                            model_floor / base,
                        )

        # Эталон может быть еще выше: топ-бида автобая MRKT — покупатель уже
        # стоит с деньгами и готов забрать подарок этой коллекции. Лот дешевле
        # такого бида — мгновенная связка «купил на входе, продал в MRKT».
        bids = await self._mrkt_top_bids()
        bid_info = bids.get(lot.gift_name)
        if bid_info and bid_info["price"] > reference:
            return bid_info["price"], "mrkt_bid"

        return reference, basis

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
            base_url = getgems_collection_url(name)
            lots.append(
                Lot(
                    gift_name=name,
                    lot_number=f"#{model_number}",
                    price=price,
                    # У mock URL общий на коллекцию; фрагмент делает его
                    # уникальным для конкретного листинга (модель + цена),
                    # иначе персистентная дедупликация глушит поток.
                    marketplace_url=f"{base_url}#model={model_number}&price={price:.2f}",
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
                raw_name = str(item.get("gift_model", {}).get("name") or "")
                gift_name = self._gift_by_name(raw_name)
                if not gift_name:
                    continue
                price = float(item["price"]) / 1e9  # nanoton -> TON
                if price <= 0:
                    continue
                number = item.get("gift_num")
                model_number = int(number) if number is not None else None
                item_id = str(item.get("id") or "")
                item_addr = item.get("gift_address") or None
                clean_addr = friendly_address(item_addr) if item_addr else None
                attrs = item.get("attributes") or {}
                model = str(attrs.get("model")) if attrs.get("model") else None
                backdrop = str(attrs.get("backdrop")) if attrs.get("backdrop") else None
                symbol = str(attrs.get("symbol")) if attrs.get("symbol") else None
                lots.append(
                    Lot(
                        gift_name=gift_name,
                        lot_number=f"#{model_number}" if model_number else "#----",
                        price=round(price, 2),
                        marketplace_url=tonnel_lot_url(item_id),
                        item_address=clean_addr,
                        image_url=fragment_model_image(gift_name, model_number) if model_number else None,
                        model_number=model_number,
                        source="tonnel",
                        model=model,
                        backdrop=backdrop,
                        symbol=symbol,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return lots

    async def _fetch_mrkt_listings(self) -> list[Lot]:
        """MRKT больше не источник покупаемых лотов: их маркет — ордера-автобай
        (ставки покупателей, заполняются подарками через orders/fill).

        Ордера используются как эталон EXIT-цены в ``reference_price``
        (см. ``_mrkt_top_bids``), а не как покупаемые лоты — поэтому здесь
        всегда пусто, чтобы движок не сравнивал бида с флором.
        """
        return []

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

__all__ = [
    "GIFT_PRESETS",
    "GIFT_NAMES",
    "LEGACY_GIFT_ALIASES",
    "Lot",
    "MarketAPI",
    "TraitPrices",
    "extract_traits",
    "friendly_address",
    "getgems_collection_url",
    "getgems_item_url",
    "mrkt_lot_url",
    "tonnel_lot_url",
    "MRKT_MINIAPP_URL",
    "PORTALS_ITEM_URL",
    "PORTALS_MARKET_URL",
    "TONNEL_MARKET_URL",
    "MRKT_API_BASE",
    "mrkt_order_url",
    "fragment_model_image",
    "name_variants",
    "normalize_collection_name",
]
