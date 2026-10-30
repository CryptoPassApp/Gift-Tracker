"""Pydantic Settings — единая точка конфигурации бота, API и движка снайпера.

Все параметры читаются из переменных окружения / файла .env.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки GiftTracker (переопределяются через .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- Telegram ----------
    BOT_TOKEN: str = "123456789:PLACEHOLDER-TOKEN-REPLACE-ME"
    # @username бота — нужен для реферальных ссылок t.me/<bot>?start=ref_<id>.
    BOT_USERNAME: str = "GiftTracker_App"
    ADMIN_IDS: list[int] = Field(default_factory=list)
    MINI_APP_URL: str = "https://example.com/"

    # ---------- Database ----------
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/gift_tracker.db"

    # ---------- Market data source ----------
    # mock           — автономный генератор (работает без внешних API)
    # tonapi         — флоры с tonapi.io (fallback на mock при ошибке)
    # giftdataset    — реальные флоры TGMrkt/Getgems/Portals из публичного
    #                  датасета TelegramGifts + реальные лоты Getgems через TonAPI
    MARKET_SOURCE: str = "mock"
    TONAPI_BASE_URL: str = "https://tonapi.io/v2"
    TONAPI_KEY: str = ""
    # JSON-строка вида {"Plush Pepe": "EQ..."} — адреса коллекций в TON.
    TONAPI_COLLECTIONS: dict[str, str] = Field(default_factory=dict)

    # ---------- Реальный рынок (без токенов) ----------
    # Публичный датасет: флоры TGMrkt, Getgems, Portals, Fragment по всем
    # подаркам. Обновляется автоматически, не требует ключей.
    GIFTDATASET_URL: str = (
        "https://raw.githubusercontent.com/ssamy2/TelegramGiftsAssests/main/Gifts_Details.json"
    )
    GIFTDATASET_TTL_SEC: float = 120.0
    # База для models/{short_name}/prices.json — флоры по каждой модели
    # (атрибут Model), фону и символу внутри коллекции.
    GIFTDATASET_BASE_URL: str = "https://raw.githubusercontent.com/ssamy2/TelegramGiftsAssests/main/"
    # TTL кэша цен по атрибутам: датасет обновляется независимо от сканера.
    TRAIT_PRICES_TTL_SEC: float = 600.0
    # Санитарный потолок: флор модели не может быть выше флора коллекции
    # больше чем в N раз, иначе это ошибка данных, а не выгодный лот.
    TRAIT_FLOOR_MAX_RATIO: float = 8.0
    # Сколько страниц по 100 NFT забирать на коллекцию за один проход TonAPI.
    # Курсор страниц двигается по кругу, чтобы за несколько проходов обойти всю коллекцию.
    TONAPI_SCAN_PAGES: int = Field(default=5, ge=1, le=50)
    TONAPI_LISTINGS_TTL_SEC: float = 60.0
    # Сколько коллекций обходить за один проход: курсор двигается по кругу,
    # чтобы не вычитывать все 119 коллекций на каждом скане.
    TONAPI_COLLECTIONS_PER_SCAN: int = Field(default=6, ge=1, le=200)
    # Минимальный интервал между запросами к TonAPI (анонимный тариф ~1 rps).
    TONAPI_MIN_REQUEST_INTERVAL_SEC: float = 1.1
    # Пауза после 429: анонимный лимит может быть выбран на сутки вперёд,
    # поэтому не долбим API каждые 5 секунд, а ждём.
    TONAPI_COOLDOWN_ON_429_SEC: float = 600.0

    # ---------- Адреса коллекций ----------
    # Публичный реестр Tonkeeper: address + name для 115 из 123 gift-коллекций.
    # Ключ не нужен, поэтому адреса не зависят от лимитов TonAPI.
    GIFT_COLLECTIONS_URL: str = (
        "https://raw.githubusercontent.com/tonkeeper/ton-assets/main/accounts.json"
    )
    # Файл-кэш: имя подарка -> адрес TON-коллекции.
    GIFT_COLLECTIONS_FILE: str = "./data/gift_collections.json"
    # Реестр перечитывается каждые 6 часов: новые подарки получают адрес
    # в тот же день, а не через неделю.
    COLLECTION_INDEX_TTL_SEC: float = 21600.0
    COLLECTION_INDEX_RETRY_SEC: float = 900.0         # повтор после сетевой ошибки

    # ---------- Источники офферов (маркетплейсы) ----------
    # getgems — ссылки/флоры без ключей; tonnel и mrkt требуют токен сессии
    # (обе площадки за Cloudflare). Через запятую: getgems,tonnel,mrkt
    OFFER_SOURCES: str = "getgems"
    # JWT из WebApp-сессии Tonnel Network (t.me/tonnel_network_bot -> /api/auth)
    TONNEL_JWT: str = ""
    # Токен сессии MRKT: POST https://api.tgmrkt.io/api/v1/auth {initData} ->
    # {token} (36 символов). Живет ~8 минут — для 24/7 используй сервис
    # scripts/mrkt_token_service.py (MTProto-цикл, пишет mrkt_token.json).
    MRKT_AUTH_TOKEN: str = ""
    # Комиссия маркета MRKT при выходе через ордер-автобай (net-формула).
    MRKT_FEE: float = 0.05
    # TTL кэша маркет-ордеров MRKT (ставки покупателей меняются быстро).
    MRKT_ORDERS_TTL_SEC: float = 60.0
    # MTProto-сессия burner-аккаунта для сервиса автообновления токена
    # (scripts/mrkt_token_service.py). Ключи с my.telegram.org, вход — ТОЛЬКО
    # с burner-аккаунта, не с основного.
    MRKT_API_ID: int = 0
    MRKT_API_HASH: str = ""
    MRKT_PHONE: str = ""
    # Куда хук /api/internal/mrkt-init кладет готовые ордера-автобай
    # (скачанные сразу после минта токена; токен наружу не выходит).
    MRKT_BIDS_FILE: str = "./data/mrkt_bids.json"
    MRKT_TOKEN_FILE: str = "./data/mrkt_token.json"
    MRKT_TOKEN_FILE_MAX_AGE_SEC: float = 900.0

    # ---------- Portals (portals.tg) ----------
    # Публичные эндпоинты Mini App без ключа: /nfts/search (до 50 лотов,
    # цена, модель/фон/символ, floor_price) и /collections/floors (123 флора).
    PORTALS_API_BASE: str = "https://app.portals.tg/api"
    PORTALS_LISTINGS_LIMIT: int = Field(default=50, ge=1, le=50)
    PORTALS_LISTINGS_TTL_SEC: float = 60.0
    PORTALS_FLOORS_TTL_SEC: float = 300.0

    # ---------- Монетизация (Telegram Stars) ----------
    PRO_MONTH_STARS: int = 150
    PRO_LIFETIME_STARS: int = 999
    PRO_MONTH_DAYS: int = 30
    # Период фонового снятия истекших PRO-подписок (секунды).
    PRO_EXPIRY_CHECK_INTERVAL_SEC: int = Field(default=3600, gt=0)
    # Рефералка: сколько процентов от подписки приглашенного уходит
    # пригласившему днями PRO (30 дней * 15% = 5 дней за месяц друга).
    REFERRAL_REWARD_PCT: int = Field(default=15, ge=1, le=100)
    # Куда ведет кнопка «Тех-поддержка» (саппорт-бот/акк; меняется в .env).
    SUPPORT_URL: str = "https://t.me/GiftTracker_App"
    # Лента сделок: сколько часов хранить snipe_signals (чистка в maintenance).
    SNIPE_SIGNALS_RETENTION_HOURS: int = Field(default=24, ge=1)

    # ---------- Монетизация (крипта через Crypto Pay @CryptoBot) ----------
    # Токен приложения Crypto Pay: @CryptoBot -> Crypto Pay -> Create App.
    # Пусто = кнопки крипто-оплаты скрыты, работает только Stars.
    CRYPTO_PAY_TOKEN: str = ""
    CRYPTO_API_BASE: str = "https://pay.crypt.bot/api"
    CRYPTO_ASSET: str = "USDT"           # валюта инвойса (USDT = цена в $)
    CRYPTO_MONTH_USD: float = 3.0        # PRO месяц в USD (эквивалент 150 Stars)
    CRYPTO_LIFETIME_USD: float = 20.0    # PRO Lifetime в USD (эквивалент 999 Stars)
    # Период фонового опроса оплаченных крипто-инвойсов (секунды).
    CRYPTO_POLL_INTERVAL_SEC: int = Field(default=120, gt=0)

    # ---------- Ограничения FREE-тарифа ----------
    FREE_PUSH_DELAY_SEC: int = 45      # задержка пушей для FREE
    FREE_ALERTS_LIMIT: int = 1         # максимум активных алертов у FREE

    # ---------- Экономика арбитража ----------
    GETGEMS_FEE: float = 0.05          # комиссия маркетплейса Getgems (5%)
    PORTALS_FEE: float = 0.02          # комиссия маркетплейса Portals (2%)
    TONNEL_FEE: float = 0.025          # комиссия маркетплейса Tonnel (2.5%)
    TON_GAS: float = 0.08              # сетевая комиссия TON
    TON_USD_RATE: float = 1.23         # живой курс TON/USD (обновляется из TonAPI, дефолт 1.23)

    def fee_for_source(self, source: str | None) -> float:
        """Комиссия конкретного маркетплейса (Portals 2%, Tonnel 2.5%, Getgems и остальные 5%)."""
        src = (source or "").strip().lower()
        if src == "portals":
            return self.PORTALS_FEE
        if src == "tonnel":
            return self.TONNEL_FEE
        if src == "mrkt":
            return self.MRKT_FEE
        return self.GETGEMS_FEE

    # ---------- Движок снайпера ----------
    SNIPER_THRESHOLD_PCT: float = 15.0   # >= 15%  -> Snipe Alert
    HOT_THRESHOLD_PCT: float = 30.0      # >= 30%  -> Hot Liquidation
    SCAN_INTERVAL_SEC: float = 5.0       # период опроса рынка
    MARKET_CACHE_TTL_SEC: float = 5.0    # TTL in-memory кэша флоров
    FEED_HISTORY_LIMIT: int = 50         # сколько сигналов отдавать в ленту
    # Сколько лотов одной коллекции максимум в ленте: иначе Plush Pepe
    # (флор 6470 TON) вытесняет все дешёвые подарки из выдачи.
    FEED_MAX_PER_GIFT: int = Field(default=5, ge=1, le=50)
    # Минимальный чистый профит (TON) для ленты: после комиссии и газа лоты
    # с +0.03 TON — мусор, на маркетах комиссия съедает такую «прибыль».
    FEED_MIN_PROFIT_TON: float = Field(default=0.2, ge=0)
    SEEN_LOTS_CACHE_SIZE: int = 500      # размер L1-кэша дедупликации лотов
    # Персистентная дедупликация в БД: не пушить один лот повторно после рестарта.
    SEEN_LOTS_RETENTION_DAYS: int = 7    # сколько дней хранить отметки о лотах

    # ---------- История флоров (24ч-изменение) ----------
    # Датасет отдает только текущий флор, историю копим сами: срез раз в
    # 30 минут, снимки живут 7 дней. Изменение показываем, только когда
    # самый старый снимок в окне 24ч старше 20 часов (иначе это не «24ч»).
    FLOOR_SNAPSHOT_INTERVAL_SEC: float = 1800.0
    FLOOR_CHANGE_MIN_AGE_HOURS: float = 20.0
    FLOOR_SNAPSHOT_RETENTION_DAYS: int = Field(default=7, ge=1)

    # ---------- Обслуживание SQLite ----------
    WAL_CHECKPOINT_INTERVAL_SEC: float = 300.0  # период PRAGMA wal_checkpoint

    # ---------- Логи ----------
    LOG_LEVEL: str = "INFO"              # DEBUG/INFO/WARNING/ERROR
    LOG_FORMAT: str = "text"             # text | json (структурные логи)

    # ---------- Heartbeat бота ----------
    # Файл-маркер живости: пишет бот, читают /api/health и Docker healthcheck.
    BOT_HEARTBEAT_FILE: str = "./data/bot_heartbeat.json"
    BOT_HEARTBEAT_INTERVAL_SEC: float = 30.0   # период записи
    BOT_HEARTBEAT_STALE_SEC: float = 120.0     # старше — считаем бота мертвым

    # ---------- Рассылка ----------
    BROADCAST_DELAY_SEC: float = 0.05    # троттлинг админ-рассылки
    NOTIFIER_CONCURRENCY: int = 25       # семафор параллельных отправок

    # ---------- API & Security ----------
    API_ALLOWED_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    INIT_DATA_MAX_AGE_SEC: int = Field(default=86400, ge=0)  # 24h по спецификации Telegram (0 = отключить)
    DEMO_RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1, le=1000)

    # ------------------------------------------------------------------
    # Валидаторы: переменные окружения приходят строками — парсим их.
    # ------------------------------------------------------------------
    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: Any) -> list[int]:
        """'123,456' или '123;456' -> [123, 456]."""
        if isinstance(value, str):
            parts = value.replace(";", ",").split(",")
            return [int(p.strip()) for p in parts if p.strip().isdigit()]
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value]
        return []

    @field_validator("TONAPI_COLLECTIONS", mode="before")
    @classmethod
    def _parse_tonapi_collections(cls, value: Any) -> dict[str, str]:
        """JSON-строка -> dict. Молча игнорируем битые значения."""
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        if isinstance(value, dict):
            return value
        return {}

    @field_validator("MRKT_API_ID", mode="before")
    @classmethod
    def _parse_mrkt_api_id(cls, value: Any) -> int:
        """Пустая строка в .env -> 0 (сервис токена не настроен)."""
        if value is None or str(value).strip() == "":
            return 0
        try:
            return int(str(value).strip())
        except ValueError:
            return 0

    @field_validator("API_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            if value.strip() == "*":
                return ["*"]
            return [p.strip() for p in value.split(",") if p.strip()]
        return value or ["*"]

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _parse_log_level(cls, value: Any) -> str:
        """Пустая строка в .env не должна ломать логирование — берем INFO."""
        if value is None:
            return "INFO"
        level = str(value).strip().upper()
        return level or "INFO"

    @field_validator("LOG_FORMAT", mode="before")
    @classmethod
    def _parse_log_format(cls, value: Any) -> str:
        """Неизвестный формат безопасно трактуем как текстовый."""
        fmt = str(value or "").strip().lower()
        return fmt if fmt in {"text", "json"} else "text"

    @property
    def plans(self) -> dict[str, dict[str, int | str]]:
        """Справочник тарифов PRO: payload инвойса -> цена и описание."""
        return {
            "pro_month": {
                "stars": self.PRO_MONTH_STARS,
                "title": "GiftTracker PRO — 1 месяц",
                "description": (
                    "Мгновенные пуши (0 сек вместо 45), безлимитные алерты, "
                    "диплинки Tonkeeper и фильтр дисконта на 30 дней."
                ),
            },
            "pro_lifetime": {
                "stars": self.PRO_LIFETIME_STARS,
                "title": "GiftTracker PRO — Lifetime",
                "description": (
                    "Пожизненный PRO: мгновенные пуши, безлимитные алерты "
                    "и все будущие функции. Оплата один раз."
                ),
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Синглтон настроек (кэшируется на процесс)."""
    return Settings()
