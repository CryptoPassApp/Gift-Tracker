"""Ловит свежий initData MRKT из открытого окна Telegram Web (ZCode IAB).

Запускай, когда в браузере ZCode открыта вкладка Telegram Web и в чате
GiftParser нажата кнопка «Купить на MRKT» (окно MRKT открыто). Скрипт
читает initData из iframe и сохраняет в data/_mrkt_init.json — дальше
сервис токена перекатывает токен сам.

Как пользоваться из консоли ZCode (я делаю это сам, руками не нужно).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "_mrkt_init.json"


def main() -> None:
    print(__doc__)


if __name__ == "__main__":
    main()
