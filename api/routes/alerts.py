"""Эндпоинты персональных алертов Mini App.

Лимиты тарифов: FREE — максимум 1 активный алерт, PRO — безлимит.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from api.dependencies import get_current_user
from bot.database import count_active_alerts
from bot.models import Alert, User
from bot.services.market_api import GIFT_NAMES

router = APIRouter(prefix="/alerts", tags=["alerts"])


async def _allowed_gift_names(request: Request) -> set[str]:
    """Все подарки из реального каталога, иначе — легаси-пресеты для mock."""
    market = getattr(request.app.state, "market", None)
    if market is not None and hasattr(market, "get_catalog"):
        if not market.get_catalog():
            await market.get_floors()
        catalog = market.get_catalog()
        if catalog:
            return set(catalog)
    return set(GIFT_NAMES)


class AlertCreate(BaseModel):
    """Тело запроса на создание алерта."""

    gift_name: str = Field(..., max_length=64, description="Например, 'Plush Pepe'")
    max_price: float = Field(..., gt=0, le=1000000, description="Триггер по максимальной цене, TON")
    min_discount: float = Field(default=15.0, ge=1, le=99, description="Минимальный дисконт, %")

    @field_validator("gift_name")
    @classmethod
    def _normalize_gift_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("gift_name must not be empty")
        return value


def _alert_to_dict(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "gift_name": alert.gift_name,
        "max_price": alert.max_price,
        "min_discount": alert.min_discount,
        "is_active": alert.is_active,
        "created_at": alert.created_at.isoformat() + "Z",
    }


@router.get("")
async def list_alerts(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Активные алерты текущего пользователя."""
    db = request.app.state.db
    async with db.session() as session:
        stmt = (
            select(Alert)
            .where(Alert.user_id == user.id, Alert.is_active.is_(True))
            .order_by(Alert.created_at.desc())
        )
        alerts = list((await session.execute(stmt)).scalars())
    return [_alert_to_dict(a) for a in alerts]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_alert(
    payload: AlertCreate,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Создает алерт (FREE ограничен одним активным триггером)."""
    settings = request.app.state.settings
    db = request.app.state.db

    if payload.gift_name not in await _allowed_gift_names(request):
        raise HTTPException(
            status_code=422, detail=f"Unknown gift: {payload.gift_name}"
        )

    async with db.session() as session:
        # Свежий объект пользователя в этой сессии (зависимость вернула detached-экземпляр).
        fresh_user = await session.get(User, user.id)
        if fresh_user is None:
            raise HTTPException(status_code=404, detail="User not found")

        used = await count_active_alerts(session, fresh_user)
        limit = None if fresh_user.is_pro_active() else settings.FREE_ALERTS_LIMIT
        if limit is not None and used >= limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"FREE plan allows only {limit} active alert. Upgrade to PRO for unlimited.",
            )

        alert = Alert(
            user_id=fresh_user.id,
            gift_name=payload.gift_name,
            max_price=payload.max_price,
            min_discount=payload.min_discount,
            is_active=True,
        )
        session.add(alert)
        await session.flush()
        return _alert_to_dict(alert)


class AlertUpdate(BaseModel):
    """Частичное обновление алерта: любое из полей."""

    gift_name: str | None = Field(default=None, max_length=64)
    max_price: float | None = Field(default=None, gt=0, le=1000000)
    min_discount: float | None = Field(default=None, ge=1, le=99)

    @field_validator("gift_name")
    @classmethod
    def _normalize_gift_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("gift_name must not be empty")
        return value


@router.patch("/{alert_id}", status_code=status.HTTP_200_OK)
async def update_alert(
    alert_id: int,
    payload: AlertUpdate,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Редактирует алерт пользователя (цена, дисконт, подарок)."""
    if payload.gift_name is not None and payload.gift_name not in await _allowed_gift_names(request):
        raise HTTPException(
            status_code=422, detail=f"Unknown gift: {payload.gift_name}"
        )

    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")

    db = request.app.state.db
    async with db.session() as session:
        alert = await session.get(Alert, alert_id)
        if alert is None or alert.user_id != user.id:
            raise HTTPException(status_code=404, detail="Alert not found")
        for field, value in changes.items():
            setattr(alert, field, value)
        await session.flush()
        return _alert_to_dict(alert)


@router.delete("/{alert_id}", status_code=status.HTTP_200_OK)
async def delete_alert(
    alert_id: int,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Удаляет алерт пользователя по id."""
    db = request.app.state.db
    async with db.session() as session:
        alert = await session.get(Alert, alert_id)
        if alert is None or alert.user_id != user.id:
            raise HTTPException(status_code=404, detail="Alert not found")
        await session.delete(alert)
        await session.flush()
    return {"ok": True, "deleted": alert_id}


__all__ = ["router"]
