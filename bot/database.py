"""Асинхронная база данных: SQLAlchemy 2.0 + aiosqlite (режим WAL).

Здесь же живут общие CRUD-хелперы (upsert пользователя, активация PRO),
используемые и ботом, и FastAPI.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot.config import Settings, get_settings
from bot.models import Alert, Base, Payment, SnipeSignal, User

logger = logging.getLogger(__name__)


def create_engine_from_settings(settings: Settings | None = None) -> AsyncEngine:
    """Создает async-engine и вешает PRAGMA на каждое соединение SQLite."""
    settings = settings or get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    return engine


async def init_db(engine: AsyncEngine) -> None:
    """Создает все таблицы, если их еще нет, и докатывает новые колонки.

    ``create_all`` не изменяет уже существующие таблицы, поэтому для
    свежих полей (image_url, item_address у snipe_signals) выполняется
    легковесная миграция через ALTER TABLE — SQLite ее поддерживает.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_patch_schema)
    logger.info("Database initialized (tables ensured)")


def _patch_schema(sync_connection) -> None:  # noqa: ANN001 — sqlalchemy callback
    """Добавляет отсутствующие колонки в существующие таблицы SQLite."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_connection)
    if not inspector.has_table("snipe_signals"):
        return  # таблицу только что создал create_all со всеми колонками

    existing = {column["name"] for column in inspector.get_columns("snipe_signals")}
    migrations = {
        "image_url": "ALTER TABLE snipe_signals ADD COLUMN image_url VARCHAR(512)",
        "item_address": "ALTER TABLE snipe_signals ADD COLUMN item_address VARCHAR(72)",
        "source": "ALTER TABLE snipe_signals ADD COLUMN source VARCHAR(16) DEFAULT 'getgems' NOT NULL",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            sync_connection.execute(text(ddl))
            logger.info("Migrated snipe_signals: added column %s", column)


def _ensure_sqlite_dir(url: str) -> None:
    """Создает родительский каталог файла SQLite, если его нет.

    SQLite не умеет создавать каталоги сам — без этого локальный запуск
    (вне Docker, где каталог создает Dockerfile) падает с
    'unable to open database file'.
    """
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    raw_path = url[len(prefix):]
    if not raw_path or raw_path.startswith(":memory:"):
        return
    # Отрезаем query-параметры (?charset=...&echo=...).
    path_part = raw_path.split("?", 1)[0]
    if not path_part:
        return
    db_path = Path(path_part)
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)


class Database:
    """Фасад над движком и фабрикой сессий — один экземпляр на процесс."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        _ensure_sqlite_dir(self.settings.DATABASE_URL)
        self.engine: AsyncEngine = create_engine_from_settings(self.settings)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def init(self) -> None:
        await init_db(self.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


# ----------------------------------------------------------------------
# Общие CRUD-хелперы
# ----------------------------------------------------------------------

async def get_or_create_user(session: AsyncSession, tg_user: dict) -> User:
    """Находит пользователя по telegram_id или создает нового.

    ``tg_user`` — словарь из Telegram (from_user / initData payload):
    обязателен ключ ``id``, опциональны ``username`` и ``first_name``.
    """
    telegram_id = int(tg_user["id"])
    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()

    username = tg_user.get("username")
    first_name = tg_user.get("first_name")

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            is_pro=False,
        )
        session.add(user)
        await session.flush()
        logger.info("New user registered: %s (%s)", telegram_id, username or first_name or "")
    else:
        user.last_active_at = datetime.utcnow()
        # Обновляем displayName-поля, если пользователь сменил их в Telegram.
        if username is not None and user.username != username:
            user.username = username
        if first_name is not None and user.first_name != first_name:
            user.first_name = first_name

    # Автоматическое понижение истекшей PRO-подписки.
    if user.is_pro and user.pro_expires_at is not None and user.pro_expires_at <= datetime.utcnow():
        user.is_pro = False
        logger.info("PRO expired for user %s", telegram_id)

    return user


async def activate_pro(session: AsyncSession, user: User, plan: str, month_days: int = 30) -> None:
    """Активирует/продлевает PRO согласно тарифу."""
    now = datetime.utcnow()
    lifetime = plan == "pro_lifetime"

    if lifetime:
        user.is_pro = True
        user.pro_expires_at = datetime(9999, 12, 31, 23, 59, 59)
    else:
        # Продление суммируется, если текущая подписка еще активна.
        base = user.pro_expires_at if (user.is_pro and user.pro_expires_at and user.pro_expires_at > now) else now
        user.is_pro = True
        user.pro_expires_at = base + timedelta(days=month_days)


async def count_active_alerts(session: AsyncSession, user: User) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count(Alert.id)).where(Alert.user_id == user.id, Alert.is_active.is_(True))
    )
    return int(result.scalar_one())


__all__ = [
    "Database",
    "init_db",
    "create_engine_from_settings",
    "get_or_create_user",
    "activate_pro",
    "count_active_alerts",
    "Alert",
    "Payment",
    "SnipeSignal",
    "User",
]
