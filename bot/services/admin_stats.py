"""Агрегаты для админ-панели и ручная выдача PRO.

Вынесено из хендлера, чтобы покрывать запросы к БД тестами без
эмуляции Telegram-сообщений:

* ``revenue_by_day`` — Stars/платежи по дням (последние N дней);
* ``revenue_by_plan`` — разбивка по тарифам;
* ``recent_payments`` — последние платежи с пользователями;
* ``grant_pro`` — ручная выдача/продление PRO по telegram_id.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database import activate_pro, get_or_create_user
from bot.models import Payment, User

# Алиасы для админской команды: /grant 123 month|lifetime.
_MANUAL_PLANS = {
    "month": "pro_month",
    "pro_month": "pro_month",
    "lifetime": "pro_lifetime",
    "pro_lifetime": "pro_lifetime",
}


def normalize_manual_plan(plan: str) -> str:
    """Приводит аргумент админа к payload тарифа или бросает ValueError."""
    normalized = _MANUAL_PLANS.get((plan or "").strip().lower())
    if normalized is None:
        raise ValueError("plan must be month|pro_month|lifetime|pro_lifetime")
    return normalized


async def revenue_by_day(
    session: AsyncSession,
    days: int = 7,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Доход по дням: [{'day': 'YYYY-MM-DD', 'payments': N, 'stars': N}]."""
    if days < 1:
        raise ValueError("days must be >= 1")
    moment = now or datetime.utcnow()
    since = (moment - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    day_expr = func.date(Payment.created_at)
    stmt = (
        select(
            day_expr.label("day"),
            func.count(Payment.id).label("payments"),
            func.coalesce(func.sum(Payment.stars_amount), 0).label("stars"),
        )
        .where(Payment.created_at >= since)
        .group_by(day_expr)
        .order_by(day_expr)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {"day": str(row.day), "payments": int(row.payments), "stars": int(row.stars)}
        for row in rows
    ]


async def revenue_by_plan(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Разбивка платежей по тарифам; отсутствующие тарифы отдаются нулями."""
    stmt = (
        select(
            Payment.plan,
            func.count(Payment.id).label("payments"),
            func.coalesce(func.sum(Payment.stars_amount), 0).label("stars"),
        )
        .group_by(Payment.plan)
    )
    result: dict[str, dict[str, int]] = {
        "pro_month": {"payments": 0, "stars": 0},
        "pro_lifetime": {"payments": 0, "stars": 0},
    }
    for row in (await session.execute(stmt)).all():
        result[str(row.plan)] = {"payments": int(row.payments), "stars": int(row.stars)}
    return result


async def recent_payments(session: AsyncSession, limit: int = 5) -> list[Payment]:
    """Последние платежи (новее — выше) вместе с пользователями."""
    safe_limit = max(1, min(int(limit), 50))
    stmt = (
        select(Payment)
        .options(selectinload(Payment.user))
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(safe_limit)
    )
    return list((await session.execute(stmt)).scalars())


async def grant_pro(
    session: AsyncSession,
    telegram_id: int,
    plan: str,
    *,
    month_days: int = 30,
) -> User:
    """Выдает PRO вручную; создает пользователя, если он еще не в БД.

    Raises:
        ValueError: неизвестный тариф или некорректный telegram_id.
    """
    if telegram_id <= 0:
        raise ValueError("telegram_id must be positive")
    normalized = normalize_manual_plan(plan)
    user = await get_or_create_user(session, {"id": telegram_id})
    await activate_pro(session, user, normalized, month_days=month_days)
    await session.flush()
    return user


__all__ = [
    "grant_pro",
    "normalize_manual_plan",
    "recent_payments",
    "revenue_by_day",
    "revenue_by_plan",
]
