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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SnipeSignal {self.gift_name!r} -{self.discount_pct}% {self.deal_price} TON>"


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
