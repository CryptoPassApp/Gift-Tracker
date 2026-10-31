"""Профиль бота в BotFather через Bot API (без ручных команд).

Ставит описание (в пустом чате), about (в профиле), short_description
(в каталоге /t.me) и список команд — дефолтные RU + переводы EN.
Идемпотентно: можно запускать повторно после смены текстов.

Запуск: python scripts/bot_profile.py  (BOT_TOKEN читается из gift_tracker/.env)
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import get_settings  # noqa: E402

DESCRIPTION_RU = (
    "GiftTracker — сканер маркетплейсов подарков Telegram. Мониторит объявления Getgems "
    "и Portals в реальном времени, находит самые выгодные предложения и считает чистую "
    "прибыль по каждой сделке с учётом комиссий площадок и сети TON.\n"
    "• Каталог 122 коллекций с ценами и историей за 24 часа\n"
    "• Калькулятор: прибыль сделки до покупки\n"
    "• Мгновенные уведомления о лучших предложениях\n"
    "PRO — уведомления без задержек и лимитов (Stars или крипта). "
    "Приглашайте друзей — получайте дни PRO."
)
DESCRIPTION_EN = (
    "GiftTracker — a scanner for Telegram gifts marketplaces. It monitors live listings on "
    "Getgems and Portals, finds the best offers and calculates the net profit of every deal "
    "after marketplace fees and TON network costs.\n"
    "• Catalog of 122 collections with up-to-date prices and 24h history\n"
    "• Deal calculator: know your profit before you buy\n"
    "• Instant alerts on the best offers\n"
    "PRO — real-time, unlimited notifications (Stars or crypto). "
    "Invite friends and earn PRO days."
)
ABOUT_RU = "Терминал снайпера подарков TG: сделки ниже флора, спреды, алерты. Рефералка +15%."
ABOUT_EN = "Telegram gifts sniper terminal: below-floor deals, spreads, alerts. Referral +15%."
SHORT_RU = "Сделки на подарки Telegram ниже флора — Getgems, Portals, чистый профит, алерты."
SHORT_EN = "Below-floor Telegram gift deals — Getgems & Portals spreads, net profit, alerts."

COMMANDS = [
    ("start", "⚡ Открыть терминал"),
    ("alerts", "🔔 Мои алерты"),
    ("pro", "👑 Купить PRO (Stars / крипта)"),
    ("ref", "🤝 Рефералка — дни PRO за друзей"),
    ("settings", "🎚 PRO-фильтр дисконта"),
    ("top", "🏆 Топ-5 снайпов радара"),
    ("help", "❓ Как это работает"),
]


def main() -> None:
    settings = get_settings()
    if not settings.BOT_TOKEN or "PLACEHOLDER" in settings.BOT_TOKEN:
        print("BOT_TOKEN not configured — abort")
        sys.exit(1)

    base = f"https://api.telegram.org/bot{settings.BOT_TOKEN}"
    calls: list[tuple[str, dict]] = [
        ("setMyDescription", {"description": DESCRIPTION_RU}),
        ("setMyDescription", {"description": DESCRIPTION_EN, "language_code": "en"}),
        # About (био в профиле) Bot API не умеет — только BotFather /setabouttext.
        ("setMyShortDescription", {"short_description": SHORT_RU}),
        ("setMyShortDescription", {"short_description": SHORT_EN, "language_code": "en"}),
        ("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]}),
    ]

    with httpx.Client(timeout=20) as client:
        for method, payload in calls:
            resp = client.post(f"{base}/{method}", json=payload)
            ok = resp.status_code == 200 and resp.json().get("ok")
            print(f"{method} ({payload.get('language_code', 'ru')}): {'OK' if ok else 'FAIL ' + resp.text[:120]}")
            if not ok:
                sys.exit(1)
    print("Bot profile updated.")


if __name__ == "__main__":
    main()
