"""Эндпоинты пользователя: профиль и создание инвойса Telegram Stars.

Для оплаты из Mini App используется Bot API ``createInvoiceLink``:
фронтенд получает ссылку и открывает ее через ``Telegram.WebApp.openInvoice``.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from api.dependencies import get_current_user
from bot.models import User

router = APIRouter(prefix="/user", tags=["user"])


class InvoiceRequest(BaseModel):
    """Запрос на создание счета."""

    plan: str = Field(..., description="pro_month | pro_lifetime")

    @field_validator("plan")
    @classmethod
    def _plan_must_exist(cls, value: str) -> str:
        if value not in {"pro_month", "pro_lifetime"}:
            raise ValueError("plan must be 'pro_month' or 'pro_lifetime'")
        return value


@router.get("/me")
async def get_me(request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Профиль текущего пользователя Mini App (+ реферальная ссылка)."""
    settings = request.app.state.settings
    db = request.app.state.db
    async with db.session() as session:
        invited = await session.scalar(
            select(func.count(User.id)).where(User.referred_by == user.id)
        )
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "is_pro": user.is_pro_active(),
        "pro_expires_at": user.pro_expires_at.isoformat() + "Z" if user.pro_expires_at else None,
        "min_discount": float(user.min_discount or 0.0),
        # Фильтр реально применяется только к PRO; FREE видит 0 как «выключен».
        "effective_min_discount": float(user.min_discount or 0.0) if user.is_pro_active() else 0.0,
        # Реферальная программа: ссылка и сколько друзей уже пришло.
        "referral_link": f"https://t.me/{settings.BOT_USERNAME}?start=ref_{user.telegram_id}",
        "invited_count": int(invited or 0),
    }


class SettingsUpdate(BaseModel):
    """Настройки PRO-фильтра дисконта."""

    min_discount: float = Field(
        ..., ge=0, le=90, description="Слать только сигналы с дисконтом >= N% (0 — выключено)"
    )


@router.post("/settings")
async def update_settings(
    payload: SettingsUpdate,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Сохраняет порог дисконта.

    Значение хранится для всех, но применяется notifier'ом только при
    активном PRO — так FREE-пользователь может заранее настроить фильтр,
    и он включится сразу после покупки подписки.
    """
    db = request.app.state.db
    async with db.session() as session:
        db_user = await session.get(User, user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        db_user.min_discount = float(payload.min_discount)
        await session.flush()
        return {
            "ok": True,
            "min_discount": float(db_user.min_discount),
            "effective_min_discount": (
                float(db_user.min_discount) if db_user.is_pro_active() else 0.0
            ),
            "is_pro": db_user.is_pro_active(),
        }


@router.post("/invoice")
async def create_invoice(
    payload: InvoiceRequest,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Создает invoice-link для оплаты PRO через Telegram Stars (XTR)."""
    settings = request.app.state.settings
    plan_info = settings.plans[payload.plan]

    body = {
        "title": plan_info["title"],
        "description": plan_info["description"],
        "payload": payload.plan,
        "provider_token": "",          # обязательно пусто для XTR
        "currency": "XTR",
        "prices": [{"label": plan_info["title"], "amount": int(plan_info["stars"])}],
    }

    url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/createInvoiceLink"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body)
            data = resp.json()
    except httpx.HTTPError:
        logger.error("Telegram createInvoiceLink network error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram API unreachable: payment service temporarily unavailable",
        ) from None

    if not data.get("ok"):
        logger.warning("Telegram createInvoiceLink rejected: %s", data.get("description", "unknown"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Telegram Stars invoice",
        )

    return {"invoice_link": data["result"], "plan": payload.plan}


__all__ = ["router"]
