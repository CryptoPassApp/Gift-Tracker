"""Внутренний хук: свежий initData MRKT -> немедленный выпуск токена.

Браузерный цикл (страница Telegram Web в ZCode IAB) раз в 60 секунд
открывает мини-апп MRKT и POST'ит сюда initData. Эмпирика: initData
пригоден для выпуска РАБОЧЕГО токена только первые ~минуту после
открытия webview — поэтому токен минтится НЕМЕДЛЕННО здесь же и
проверяется живым запросом ордеров; в файл пишется только рабочий.

data/mrkt_token.json читает MarketAPI сканера и API.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
SECRET_FILE = DATA / "_mrkt_hook_secret"
INIT_FILE = DATA / "_mrkt_init.json"
BIDS_FILE = DATA / "mrkt_bids.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Origin": "https://cdn.tgmrkt.io",
    "Referer": "https://cdn.tgmrkt.io/",
}


def _secret() -> str:
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(16), encoding="utf-8")
    return SECRET_FILE.read_text(encoding="utf-8").strip()


async def _mint_and_fetch_bids(init_data: str, user, user_id) -> dict | None:
    """initData -> токен -> сразу качаем маркет-ордера. None = токен мертв.

    Токен живет секунды внеhttp-сессии минта, поэтому ордера качаем ЗДЕСЬ
    же и отдаем сканеру готовые бида (data/mrkt_bids.json) — сам токен
    наружу не покидает процесс API.
    """
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as http:
        resp = await http.post(
            "https://api.tgmrkt.io/api/v1/auth",
            json={
                "data": init_data,
                "photo": None,
                "appId": None,
                "user": user,
                "userId": user_id,
            },
        )
        if resp.status_code != 200:
            logger.warning("MRKT auth HTTP %s", resp.status_code)
            return None
        token = (resp.json() or {}).get("token") or ""
        if not token:
            return None
        auth_headers = {**HEADERS, "Authorization": token}

        probe = await http.post(
            "https://api.tgmrkt.io/api/v1/orders",
            json={
                "CollectionNames": [], "ModelNames": [], "BackdropNames": [],
                "SymbolNames": [], "cursor": "", "count": 50,
            },
            headers=auth_headers,
        )
        if probe.status_code != 200:
            logger.info("MRKT токен не прошел пробу (HTTP %s) — initData стар", probe.status_code)
            return None
        orders = (probe.json() or {}).get("orders") or []
        return orders


@router.post("/mrkt-init")
async def receive_mrkt_init(request: Request) -> dict[str, str]:
    """Свежий initData -> токен -> ордера -> готовые бида для сканера."""
    if request.headers.get("x-mrkt-hook", "") != _secret():
        return {"status": "forbidden"}

    body = (await request.body()).decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except ValueError:
        return {"status": "bad json"}

    init_data = str(payload.get("initData") or "").strip()
    if not init_data:
        return {"status": "no initData"}

    user = payload.get("user")
    user_id = payload.get("userId")

    DATA.mkdir(parents=True, exist_ok=True)
    INIT_FILE.write_text(
        json.dumps(
            {"initData": init_data, "user": user, "userId": user_id, "capturedAt": time.time()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    orders = await _mint_and_fetch_bids(init_data, user, user_id)
    if orders is None:
        logger.info("MRKT initData принят, но ордера не получены (initData стар)")
        return {"status": "init kept, orders stale"}

    DATA.mkdir(parents=True, exist_ok=True)
    BIDS_FILE.write_text(
        json.dumps({"orders": orders, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("MRKT initData принят, ордера обновлены (n=%d)", len(orders))
    return {"status": "ok", "orders": str(len(orders))}
