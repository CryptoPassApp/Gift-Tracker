"""Одноразовый вход MTProto-сессии для MRKT Token Service.

Запусти в ВИДИМОМ окне консоли (start cmd /k python scripts\\mrkt_login.py):
спросит номер burner-аккаунта и код из Telegram — и сохранит сессию в
data/mrkt_session.session. После этого сервис mrkt_token_service.py
работает без консоли.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telethon import TelegramClient  # noqa: E402

from scripts.mrkt_token_service import _load_dotenv  # noqa: E402


async def main() -> None:
    env = _load_dotenv(ROOT / ".env")
    api_id = int(env.get("MRKT_API_ID") or 0)
    api_hash = env.get("MRKT_API_HASH") or ""
    phone = env.get("MRKT_PHONE") or ""
    if not api_id or not api_hash:
        print("Заполни MRKT_API_ID и MRKT_API_HASH в .env (my.telegram.org).")
        print("ВАЖНО: ключи и вход — только с BURNER-аккаунта, не с основного.")
        return

    client = TelegramClient(
        str(ROOT / "data" / "mrkt_session"), api_id, api_hash,
        device_model="GiftTracker", system_version="1.0",
    )
    await client.start(phone=phone or None)
    me = await client.get_me()
    print(f"Сессия сохранена: id={getattr(me, 'id', '?')} (@{getattr(me, 'username', None)})")
    print("Теперь запусти start_local.ps1 — сервис токена заработает.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
