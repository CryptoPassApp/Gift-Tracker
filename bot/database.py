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

from sqlalchemy import delete, event, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bot.config import Settings, get_settings
from bot.models import Alert, Base, CryptoPayment, FloorSnapshot, Payment, ReferralReward, SeenLot, SnipeSignal, User

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
    """Добавляет отсутствующие колонки и чинит старые имена подарков."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_connection)
    migrations: dict[str, dict[str, str]] = {
        "snipe_signals": {
            "image_url": "ALTER TABLE snipe_signals ADD COLUMN image_url VARCHAR(512)",
            "item_address": "ALTER TABLE snipe_signals ADD COLUMN item_address VARCHAR(72)",
            "source": "ALTER TABLE snipe_signals ADD COLUMN source VARCHAR(16) DEFAULT 'getgems' NOT NULL",
            "model": "ALTER TABLE snipe_signals ADD COLUMN model VARCHAR(64)",
            "backdrop": "ALTER TABLE snipe_signals ADD COLUMN backdrop VARCHAR(64)",
            "symbol": "ALTER TABLE snipe_signals ADD COLUMN symbol VARCHAR(64)",
            "floor_basis": "ALTER TABLE snipe_signals ADD COLUMN floor_basis VARCHAR(96)",
        },
        "users": {
            "min_discount": "ALTER TABLE users ADD COLUMN min_discount REAL DEFAULT 0 NOT NULL",
            "referred_by": "ALTER TABLE users ADD COLUMN referred_by INTEGER",
        },
    }
    for table, columns in migrations.items():
        if not inspector.has_table(table):
            continue  # таблицу только что создал create_all со всеми колонками
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column, ddl in columns.items():
            if column not in existing:
                sync_connection.execute(text(ddl))
                logger.info("Migrated %s: added column %s", table, column)

    _rename_legacy_gifts(sync_connection)
    _migrate_getgems_urls(sync_connection)
    _purge_fake_model_signals(sync_connection)


def _purge_fake_model_signals(sync_connection) -> None:  # noqa: ANN001 — sqlalchemy callback
    """Удаляет фейковые сигналы, рассчитанные по неликвидным ценам моделей из датасета."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_connection)
    if not inspector.has_table("snipe_signals"):
        return

    result = sync_connection.execute(
        text(
            "DELETE FROM snipe_signals WHERE floor_basis != 'collection' AND gift_name != 'Plush Pepe'"
        )
    )
    if result.rowcount:
        logger.info("Purged %d fake model signals from database", result.rowcount)


def _migrate_getgems_urls(sync_connection) -> None:  # noqa: ANN001 — sqlalchemy callback
    """Нормализует адреса Getgems (0:hex -> EQ...) в сохраненных сигналах."""
    from sqlalchemy import inspect, text

    from bot.services.market_api import friendly_address

    inspector = inspect(sync_connection)
    if not inspector.has_table("snipe_signals"):
        return

    cursor = sync_connection.execute(
        text(
            "SELECT id, marketplace_url, item_address FROM snipe_signals "
            "WHERE source = 'getgems' AND (marketplace_url LIKE '%0:%' OR item_address LIKE '0:%')"
        )
    )
    rows = cursor.fetchall()
    updated = 0
    for row_id, m_url, i_addr in rows:
        new_url = m_url
        if m_url and "getgems.io/nft/" in m_url:
            base = m_url.split("?")[0]
            prefix, _, addr = base.partition("getgems.io/nft/")
            if addr and ":" in addr:
                new_url = f"{prefix}getgems.io/nft/{friendly_address(addr)}"
        new_addr = friendly_address(i_addr) if (i_addr and ":" in i_addr) else i_addr
        if new_url != m_url or new_addr != i_addr:
            sync_connection.execute(
                text("UPDATE snipe_signals SET marketplace_url = :url, item_address = :addr WHERE id = :id"),
                {"url": new_url, "addr": new_addr, "id": row_id},
            )
            updated += 1
    if updated:
        logger.info("Migrated %d Getgems signals to user-friendly EQ format", updated)


def _rename_legacy_gifts(sync_connection) -> None:  # noqa: ANN001 — sqlalchemy callback
    """Переводит выдуманные имена подарков (Magic Hat, ...) на канонические.

    Раньше пресеты назывались алиасами, которых нет у Telegram: алерты и
    история сигналов хранят именно их. Без миграции пользовательские алерты
    перестали бы срабатывать после перехода на реальные имена.
    """
    from sqlalchemy import inspect, text

    from bot.services.market_api import LEGACY_GIFT_ALIASES

    inspector = inspect(sync_connection)
    for table in ("alerts", "snipe_signals"):
        if not inspector.has_table(table):
            continue
        for old_name, new_name in LEGACY_GIFT_ALIASES.items():
            result = sync_connection.execute(
                text(f"UPDATE {table} SET gift_name = :new WHERE gift_name = :old"),
                {"new": new_name, "old": old_name},
            )
            if result.rowcount:
                logger.info(
                    "Migrated %s: gift_name %r -> %r (%d rows)",
                    table,
                    old_name,
                    new_name,
                    result.rowcount,
                )


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
        # Время последнего снимка флоров (прореживаем запись в floor_snapshots).
        self._last_floor_snapshot_at: datetime | None = None

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

    # ------------------------------------------------------------------
    # Дедупликация лотов (переживает рестарт процесса)
    # ------------------------------------------------------------------
    async def claim_seen_lot(self, url: str) -> bool:
        """Атомарно «застолбить» лот.

        Возвращает ``True`` только первому вызову для данного URL:
        ``INSERT ... ON CONFLICT DO NOTHING`` в SQLite. Благодаря этому
        после рестарта бот не пушит те же лоты повторно.
        """
        async with self.session() as session:
            stmt = (
                sqlite_insert(SeenLot)
                .values(url=url, seen_at=datetime.utcnow())
                .on_conflict_do_nothing(index_elements=["url"])
            )
            result = await session.execute(stmt)
            return bool(result.rowcount)

    async def prune_seen_lots(self, retention_days: int | None = None) -> int:
        """Удаляет отметки старше срока хранения; возвращает число строк."""
        days = self.settings.SEEN_LOTS_RETENTION_DAYS if retention_days is None else retention_days
        if days <= 0:
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with self.session() as session:
            result = await session.execute(delete(SeenLot).where(SeenLot.seen_at < cutoff))
            return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # История флоров (изменение за 24ч)
    # ------------------------------------------------------------------
    async def record_floor_snapshot(
        self, floors: dict[str, float], min_interval_sec: float = 1800.0
    ) -> int:
        """Пишет срез флоров, если с прошлого прошло не меньше интервала."""
        values = {name: float(floor) for name, floor in floors.items() if floor and floor > 0}
        if not values:
            return 0
        now = datetime.utcnow()
        if self._last_floor_snapshot_at is not None:
            elapsed = (now - self._last_floor_snapshot_at).total_seconds()
            if elapsed < min_interval_sec:
                return 0
        self._last_floor_snapshot_at = now
        async with self.session() as session:
            session.add_all(
                FloorSnapshot(gift_name=name, floor=floor, created_at=now)
                for name, floor in values.items()
            )
        return len(values)

    async def floor_changes(
        self,
        current: dict[str, float],
        *,
        window_hours: float = 24.0,
        min_age_hours: float = 20.0,
    ) -> dict[str, float | None]:
        """Изменение флора за окно в % относительно самого старого снимка.

        Возвращает ``None``, если история короче ``min_age_hours``: показывать
        «▲ 0.0%» на данных пятиминутной давности — это выдумка, а не анализ.
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=window_hours)
        min_age = now - timedelta(hours=min_age_hours)
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(
                        FloorSnapshot.gift_name,
                        FloorSnapshot.floor,
                        FloorSnapshot.created_at,
                    )
                    .where(FloorSnapshot.created_at >= cutoff)
                    .order_by(FloorSnapshot.created_at.asc())
                )
            ).all()

        oldest: dict[str, tuple[float, datetime]] = {}
        for gift_name, floor, created_at in rows:
            oldest.setdefault(gift_name, (float(floor), created_at))

        result: dict[str, float | None] = {}
        for name, value in current.items():
            base = oldest.get(name)
            if base is None or base[1] > min_age or base[0] <= 0:
                result[name] = None
                continue
            result[name] = round((value - base[0]) / base[0] * 100, 1)
        return result

    async def prune_floor_snapshots(self, retention_days: int = 7) -> int:
        """Удаляет снимки старше срока хранения; возвращает число строк."""
        if retention_days <= 0:
            return 0
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        async with self.session() as session:
            result = await session.execute(
                delete(FloorSnapshot).where(FloorSnapshot.created_at < cutoff)
            )
            return int(result.rowcount or 0)

    async def prune_snipe_signals(self, retention_hours: int = 24) -> int:
        """Чистит ленту сделок (snipe_signals) старше срока хранения.

        Лента — «живая» витрина: сделки старше суток не имеют ценности,
        а таблица растет при каждом скане (иначе бесконечный рост).
        Дедупликация пушей живет в отдельной ``seen_lots`` и не страдает.
        """
        if retention_hours <= 0:
            return 0
        cutoff = datetime.utcnow() - timedelta(hours=retention_hours)
        async with self.session() as session:
            result = await session.execute(
                delete(SnipeSignal).where(SnipeSignal.created_at < cutoff)
            )
            deleted = int(result.rowcount or 0)
        if deleted:
            logger.info("Pruned %d snipe_signal(s) older than %dh", deleted, retention_hours)
        return deleted

    # ------------------------------------------------------------------
    # Жизненный цикл PRO
    # ------------------------------------------------------------------
    async def expire_pro_subscriptions(self, now: datetime | None = None) -> int:
        """Снимает ``is_pro`` у истекших подписок; возвращает число юзеров.

        Lifetime-подписки хранят дату 9999-12-31 и под условие не попадают.
        Ленивая проверка в ``get_or_create_user`` остается, но фоновая джоба
        гарантирует, что истекший PRO теряет права даже без визита в бота.
        """
        moment = now or datetime.utcnow()
        async with self.session() as session:
            result = await session.execute(
                update(User)
                .where(
                    User.is_pro.is_(True),
                    User.pro_expires_at.is_not(None),
                    User.pro_expires_at <= moment,
                )
                .values(is_pro=False)
            )
            expired = int(result.rowcount or 0)
        if expired:
            logger.info("Expired PRO subscriptions downgraded: %d", expired)
        return expired

    # ------------------------------------------------------------------
    # Крипто-платежи (Crypto Pay @CryptoBot)
    # ------------------------------------------------------------------
    async def create_crypto_invoice(
        self, invoice_id: int, user_id: int, plan: str, asset: str, amount: float
    ) -> bool:
        """Запоминает созданный инвойс; False — такой invoice_id уже есть."""
        async with self.session() as session:
            existing = await session.get(CryptoPayment, invoice_id)
            if existing is not None:
                return False
            session.add(
                CryptoPayment(
                    invoice_id=invoice_id,
                    user_id=user_id,
                    plan=plan,
                    asset=asset,
                    amount=amount,
                    status="active",
                )
            )
        return True

    async def mark_crypto_paid(self, invoice_id: int, paid_at: datetime | None = None) -> CryptoPayment | None:
        """Переводит инвойс в ``paid``; None — инвойс не найден в БД.

        Повторный вызов для уже оплаченного инвойса ничего не меняет —
        идемпотентность синхронизации (двойное начисление PRO невозможно).
        """
        moment = paid_at or datetime.utcnow()
        async with self.session() as session:
            payment = await session.get(CryptoPayment, invoice_id)
            if payment is None:
                return None
            if payment.status == "paid":
                return payment
            payment.status = "paid"
            payment.paid_at = moment
            await session.flush()
            session.expunge(payment)
        logger.info("Crypto invoice #%d marked paid (%s)", invoice_id, payment.plan)
        return payment

    async def active_crypto_invoices(self) -> list[CryptoPayment]:
        """Все инвойсы в статусе ``active`` — кандидаты на перепроверку."""
        async with self.session() as session:
            result = await session.execute(
                select(CryptoPayment).where(CryptoPayment.status == "active")
            )
            return list(result.scalars().all())

    async def get_crypto_invoice(self, invoice_id: int) -> CryptoPayment | None:
        """Один инвойс по id (для кнопки «Я уже оплатил»)."""
        async with self.session() as session:
            return await session.get(CryptoPayment, invoice_id)

    async def user_by_id(self, user_id: int) -> User | None:
        """Пользователь по внутреннему id (нужен синхронизации платежей)."""
        async with self.session() as session:
            return await session.get(User, user_id)

    # ------------------------------------------------------------------
    # Обслуживание SQLite
    # ------------------------------------------------------------------
    async def checkpoint_wal(self, mode: str = "TRUNCATE") -> dict[str, int] | None:
        """Выполняет ``PRAGMA wal_checkpoint`` (по умолчанию с усечением WAL).

        Возвращает служебные счетчики SQLite ``busy/log/checkpointed``
        или ``None`` для не-SQLite баз. ``busy=1`` означает, что в момент
        вызова были активные читатели — следующий запуск повторит попытку.
        """
        normalized = (mode or "TRUNCATE").upper()
        if normalized not in _ALLOWED_CHECKPOINT_MODES:
            raise ValueError(f"Unsupported WAL checkpoint mode: {mode!r}")
        if not self.settings.DATABASE_URL.startswith("sqlite"):
            return None

        async with self.engine.begin() as conn:
            result = await conn.exec_driver_sql(f"PRAGMA wal_checkpoint({normalized})")
            row = result.fetchone()
        if row is None:
            return None
        return {"busy": int(row[0]), "log": int(row[1]), "checkpointed": int(row[2])}


_ALLOWED_CHECKPOINT_MODES = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}


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
    elif user.is_pro and user.pro_expires_at and user.pro_expires_at.year == 9999:
        # Покупка месяца поверх Lifetime не должна падать на переполнении даты.
        user.is_pro = True
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


async def award_referral(
    session: AsyncSession,
    referred_user: User,
    payment_ref: str,
    source: str,
    settings: Settings | None = None,
) -> tuple[User, float] | None:
    """Начисляет пригласившему награду за оплату PRO приглашенным.

    Возвращает ``(referrer, days)`` или ``None``, если начислять не за что:
    юзер без пригласившего, самореферал или этот платеж уже засчитан.

    Защита (в связке с start.py): ``referred_by`` ставится один раз и только
    по реальному deep-link, самореферал отклонен, ``payment_ref`` уникален —
    повторная доставка того же платежа не дает второй бонус.
    """
    if not referred_user.referred_by:
        return None
    referrer = await session.get(User, referred_user.referred_by)
    if referrer is None or referrer.id == referred_user.id:
        return None

    existing = (
        await session.execute(
            select(ReferralReward).where(ReferralReward.payment_ref == payment_ref)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    from sqlalchemy import func
    from math import ceil

    settings = settings or get_settings()
    days = float(ceil(settings.PRO_MONTH_DAYS * settings.REFERRAL_REWARD_PCT / 100))
    session.add(
        ReferralReward(
            referrer_user_id=referrer.id,
            referred_user_id=referred_user.id,
            payment_ref=payment_ref,
            days_awarded=days,
            source=source,
        )
    )
    # Дни поверх текущей подписки (Lifetime-юзер останется Lifetime —
    # его подписка и так вечная, дублировать нечего).
    await activate_pro(session, referrer, "pro_month", month_days=days)
    logger.info(
        "Referral reward: referrer=%s +%gd PRO (from user %s, %s)",
        referrer.telegram_id,
        days,
        referred_user.telegram_id,
        payment_ref,
    )
    return referrer, days


async def referral_stats(session: AsyncSession, user: User) -> tuple[int, float]:
    """Сколько юзеров пригласил пользователь и суммарно заработанных дней."""
    from sqlalchemy import func

    invited = (
        await session.execute(
            select(func.count(User.id)).where(User.referred_by == user.id)
        )
    ).scalar_one()
    earned = (
        await session.execute(
            select(func.coalesce(func.sum(ReferralReward.days_awarded), 0.0)).where(
                ReferralReward.referrer_user_id == user.id
            )
        )
    ).scalar_one()
    return int(invited), float(earned)


__all__ = [
    "Database",
    "init_db",
    "create_engine_from_settings",
    "get_or_create_user",
    "activate_pro",
    "count_active_alerts",
    "Alert",
    "FloorSnapshot",
    "Payment",
    "SeenLot",
    "SnipeSignal",
    "User",
]
