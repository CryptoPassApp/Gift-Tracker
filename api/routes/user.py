"""Эндпоинты пользователя: профиль и создание инвойса Telegram Stars.

Для оплаты из Mini App используется Bot API ``createInvoiceLink``:
фронтенд получает ссылку и открывает ее через ``Telegram.WebApp.openInvoice``.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

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
async def get_me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Профиль текущего пользователя Mini App."""
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "is_pro": user.is_pro_active(),
        "pro_expires_at": user.pro_expires_at.isoformat() + "Z" if user.pro_expires_at else None,
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
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Telegram API unreachable: {exc}",
        ) from exc

    if not data.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Telegram API error: {data.get('description', 'unknown')}",
        )

    return {"invoice_link": data["result"], "plan": payload.plan}


__all__ = ["router"]
