"""ORM-модели GiftTracker: User, Alert, SnipeSignal, Payment.

Схема соответствует ЧАСТИ 4 техзадания (SQLite WAL).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""


def _utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    """Пользователь бота / Mini App."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_pro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pro_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # PRO-фильтр: не присылать broadcast-сигналы с дисконтом ниже порога (0 = без фильтра).
    min_discount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Реферал: id пользователя, который пригласил (deep-link /start ref_<tg_id>).
    referred_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def is_pro_active(self) -> bool:
        """PRO активен, если флаг поднят и подписка не истекла."""
        if not self.is_pro:
            return False
        if self.pro_expires_at is None:
            return True
        return self.pro_expires_at > _utcnow()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} tg={self.telegram_id} pro={self.is_pro}>"


class Alert(Base):
    """Персональный алерт пользователя на конкретный подарок."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gift_name: Mapped[str] = mapped_column(String(64), nullable=False)
    max_price: Mapped[float] = mapped_column(Float, nullable=False)
    min_discount: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="alerts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Alert id={self.id} gift={self.gift_name!r} max={self.max_price}>"


class SnipeSignal(Base):
    """Найденный снайпером лот (история для ленты Mini App)."""

    __tablename__ = "snipe_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gift_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lot_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    floor_price: Mapped[float] = mapped_column(Float, nullable=False)
    deal_price: Mapped[float] = mapped_column(Float, nullable=False)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False)
    net_profit_ton: Mapped[float] = mapped_column(Float, nullable=False)
    marketplace_url: Mapped[str] = mapped_column(String(256), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)    # рендер модели (CDN Fragment)
    item_address: Mapped[str | None] = mapped_column(String(72), nullable=True)  # адрес NFT в TON (raw 0:...)
    source: Mapped[str] = mapped_column(String(16), default="getgems", nullable=False)  # getgems|tonnel|mrkt
    # Атрибуты конкретного NFT: чем один лот отличается от другого.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backdrop: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # На чем основан floor_price: "collection" или "model:<имя>".
    floor_basis: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SnipeSignal {self.gift_name!r} -{self.discount_pct}% {self.deal_price} TON>"


class FloorSnapshot(Base):
    """Снимок флора коллекции — база для честного изменения за 24ч.

    Датасет отдает только текущие цены, поэтому история копится самим
    сервисом: API периодически пишет срез всех флоров, а изменение
    считается относительно самого старого снимка в окне 24ч.
    """

    __tablename__ = "floor_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gift_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    floor: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FloorSnapshot {self.gift_name!r} {self.floor} at {self.created_at}>"


class SeenLot(Base):
    """Персистентная отметка «этот лот уже обработан» (дедупликация пушей).

    Ключ — ``marketplace_url`` лота: у реальных маркетплейсов URL уникален
    для каждого листинга. У mock-генератора URL коллекции общий, поэтому
    для него добавляется фрагмент с моделью и ценой (см. market_api).
    Отметки старше ``SEEN_LOTS_RETENTION_DAYS`` вычищаются обслуживанием.
    """

    __tablename__ = "seen_lots"

    url: Mapped[str] = mapped_column(String(512), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SeenLot {self.url[:48]!r} at {self.seen_at}>"


class Payment(Base):
    """Покупка PRO-подписки за Telegram Stars."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    telegram_charge_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)  # pro_month | pro_lifetime
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="payments")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Payment {self.plan} {self.stars_amount}★ charge={self.telegram_charge_id}>"


class CryptoPayment(Base):
    """Крипто-инвойс Crypto Pay (@CryptoBot): разовый платеж за PRO.

    ``invoice_id`` — первичный ключ от Crypto Pay API, поэтому повторная
    синхронизация того же инвойса не создает вторую запись (идемпотентность).
    Жизненный цикл статуса: ``active`` -> ``paid`` (или ``expired``).
    """

    __tablename__ = "crypto_payments"

    invoice_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)  # pro_month | pro_lifetime
    asset: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CryptoPayment #{self.invoice_id} {self.plan} {self.amount} {self.asset} {self.status}>"


class ReferralReward(Base):
    """Награда пригласившему за оплату PRO приглашенным пользователем.

    Защита от абьюза: ``payment_ref`` уникален («stars:<charge_id>» /
    «crypto:<invoice_id>»), поэтому один и тот же платеж не может
    начислить бонус дважды; самореферал и повторное переприсвоение
    ``referred_by`` отсечены на уровне логики (start.py / database.py).
    """

    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    referred_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    payment_ref: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    days_awarded: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # stars | crypto
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ReferralReward ref={self.referrer_user_id} +{self.days_awarded}d {self.payment_ref}>"
