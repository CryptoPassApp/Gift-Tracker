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
    ADMIN_IDS: list[int] = Field(default_factory=list)
    MINI_APP_URL: str = "https://example.com/"

    # ---------- Database ----------
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/gift_tracker.db"

    # ---------- Market data source ----------
    # mock  — автономный генератор (работает без внешних API, по умолчанию)
    # tonapi — попытка получать флоры с tonapi.io (fallback на mock при ошибке)
    MARKET_SOURCE: str = "mock"
    TONAPI_BASE_URL: str = "https://tonapi.io/v2"
    TONAPI_KEY: str = ""
    # JSON-строка вида {"Plush Pepe": "EQ..."} — адреса коллекций в TON.
    TONAPI_COLLECTIONS: dict[str, str] = Field(default_factory=dict)

    # ---------- Источники офферов (маркетплейсы) ----------
    # getgems — ссылки/флоры без ключей; tonnel и mrkt требуют токен сессии
    # (обе площадки за Cloudflare). Через запятую: getgems,tonnel,mrkt
    OFFER_SOURCES: str = "getgems"
    # JWT из WebApp-сессии Tonnel Network (t.me/tonnel_network_bot -> /api/auth)
    TONNEL_JWT: str = ""
    # Токен сессии MRKT
    MRKT_AUTH_TOKEN: str = ""

    # ---------- Монетизация (Telegram Stars) ----------
    PRO_MONTH_STARS: int = 150
    PRO_LIFETIME_STARS: int = 999
    PRO_MONTH_DAYS: int = 30

    # ---------- Ограничения FREE-тарифа ----------
    FREE_PUSH_DELAY_SEC: int = 45      # задержка пушей для FREE
    FREE_ALERTS_LIMIT: int = 1         # максимум активных алертов у FREE

    # ---------- Экономика арбитража ----------
    GETGEMS_FEE: float = 0.05          # комиссия маркетплейса Getgems (5%)
    TON_GAS: float = 0.08              # сетевая комиссия TON
    TON_USD_RATE: float = 5.20         # курс для отображения в Mini App

    # ---------- Движок снайпера ----------
    SNIPER_THRESHOLD_PCT: float = 15.0   # >= 15%  -> Snipe Alert
    HOT_THRESHOLD_PCT: float = 30.0      # >= 30%  -> Hot Liquidation
    SCAN_INTERVAL_SEC: float = 5.0       # период опроса рынка
    MARKET_CACHE_TTL_SEC: float = 5.0    # TTL in-memory кэша флоров
    FEED_HISTORY_LIMIT: int = 50         # сколько сигналов отдавать в ленту
    SEEN_LOTS_CACHE_SIZE: int = 500      # размер кэша дедупликации лотов

    # ---------- Рассылка ----------
    BROADCAST_DELAY_SEC: float = 0.05    # троттлинг админ-рассылки
    NOTIFIER_CONCURRENCY: int = 25       # семафор параллельных отправок

    # ---------- API ----------
    API_ALLOWED_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])

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

    @field_validator("API_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            if value.strip() == "*":
                return ["*"]
            return [p.strip() for p in value.split(",") if p.strip()]
        return value or ["*"]

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
