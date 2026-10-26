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


class AlertCreate(BaseModel):
    """Тело запроса на создание алерта."""

    gift_name: str = Field(..., max_length=64, description="Например, 'Plush Pepe'")
    max_price: float = Field(..., gt=0, le=10000, description="Триггер по максимальной цене, TON")
    min_discount: float = Field(default=15.0, ge=1, le=99, description="Минимальный дисконт, %")

    @field_validator("gift_name")
    @classmethod
    def _gift_must_exist(cls, value: str) -> str:
        if value not in GIFT_NAMES:
            raise ValueError(f"Unknown gift: {value}. Allowed: {', '.join(GIFT_NAMES)}")
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
