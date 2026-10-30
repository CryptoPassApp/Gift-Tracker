"""MRKT Token Service v2 — MTProto-цикл, БЕЗ браузера и без сторонних ботов.

Один раз в MRKT_TOKEN_REFRESH_SEC секунд:
  1. Открывает вебвью MRKT через MTProto (requestAppWebView) — Telegram
     выдает СВЕЖИЙ initData (браузер и вкладки не нужны);
  2. Меняет initData на токен (POST /api/v1/auth) и СРАЗУ скачивает
     маркет-ордера (токен живет секунды вне сессии минта);
  3. Пишет готовые бида в data/mrkt_bids.json — их читает MarketAPI.

MTProto-ключ берется из data/_tg_web_session.json (hex auth_key + dc),
извлеченный один раз из web.telegram.org. api_id 6 = официальный клиент
Telegram Web; это тот же аккаунт, что авторизован в веб-версии.

ВАЖНО (для продакшна у покупателя): ключ сессии = полный доступ к аккаунту.
Для прода сгенерируй отдельный ключ через QR-вход burner-аккаунта
(тот же сервис умеет: положи ключ в файл — логика та же).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import get_settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mrkt-token")

DC_IP = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100",
         4: "149.154.167.91", 5: "91.108.56.130"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Origin": "https://cdn.tgmrkt.io",
    "Referer": "https://cdn.tgmrkt.io/",
}


def _load_session(path: Path) -> tuple[int, bytes]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    raw = str(blob["key"]).strip().strip('"').strip()
    dc = int(blob.get("dcNum") or blob.get("dc") or 2)
    return dc, bytes.fromhex(raw)


async def _mint_and_fetch(client: TelegramClient) -> list[dict]:
    """requestAppWebView -> initData -> токен -> ордера (все в одной сессии)."""
    peer = await client.get_input_entity("mrkt")
    result = await client(
        RequestAppWebViewRequest(
            peer=peer,
            app=InputBotAppShortName(peer, "app"),
            start_param="",
            platform="android",
        )
    )
    url = result.url
    init_data = parse_qs(url[url.index("#") + 1:]).get("tgWebAppData", [""])[0]
    if not init_data:
        raise RuntimeError("пустой tgWebAppData")

    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as http:
        a = await http.post(
            "https://api.tgmrkt.io/api/v1/auth",
            json={"data": init_data, "photo": None, "appId": None, "user": None, "userId": None},
        )
        if a.status_code != 200:
            raise RuntimeError(f"MRKT auth HTTP {a.status_code}")
        token = (a.json() or {}).get("token") or ""
        if not token:
            raise RuntimeError("пустой token")
        o = await http.post(
            "https://api.tgmrkt.io/api/v1/orders",
            json={
                "CollectionNames": [], "ModelNames": [], "BackdropNames": [],
                "SymbolNames": [], "cursor": "", "count": 50,
            },
            headers={"Authorization": token},
        )
        if o.status_code != 200:
            raise RuntimeError(f"MRKT orders HTTP {o.status_code}")
        return (o.json() or {}).get("orders") or []


async def main() -> None:
    settings = get_settings()
    refresh_sec = float(settings.MRKT_TOKEN_REFRESH_SEC or 120)
    session_file = ROOT / "data" / "_tg_web_session.json"
    bids_file = ROOT / "data" / "mrkt_bids.json"
    bids_file.parent.mkdir(parents=True, exist_ok=True)

    if not session_file.exists():
        log.error(
            "Нет %s — ключ веб-сессии Telegram не извлечен. "
            "Сессия извлекается один раз из web.telegram.org.",
            session_file.name,
        )
        return

    dc, key_bytes = _load_session(session_file)
    session = StringSession()
    session.set_dc(dc, DC_IP.get(dc, "149.154.167.51"), 443)
    from telethon.crypto import AuthKey
    session.auth_key = AuthKey(key_bytes)
    client = TelegramClient(
        session, 6, "eb06d4abfb49dc3eeb1aeb98ae0f581e",
        device_model="Telegram Web A", system_version="linux", app_version="1.0",
    )
    await client.connect()
    if not await client.is_user_authorized():
        log.error("MTProto-ключ не авторизован — извлеки сессию из web.telegram.org заново")
        return
    me = await client.get_me()
    log.info("MTProto-сессия активна: id=%s", getattr(me, "id", "?"))

    while True:
        started = time.monotonic()
        try:
            orders = await _mint_and_fetch(client)
            bids_file.write_text(
                json.dumps({"orders": orders, "ts": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("Ордера MRKT обновлены (n=%d)", len(orders))
        except Exception as exc:  # noqa: BLE001 — сервис не должен падать
            log.warning("Обновление не удалось: %s", exc)

        elapsed = time.monotonic() - started
        await asyncio.sleep(max(30.0, refresh_sec - elapsed))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
